# Chaogu-Alert 功能演进设计

**日期**: 2026-04-28
**状态**: 设计中

---

## 概述

四个子系统：多账号架构改造 → 策略绩效追踪 → 实盘交易 OCR 记录 → 策略 vs 实盘对比分析。

### 架构总览

```
accounts (账号管理)
    ↓ account_id 贯穿所有表
    ├── holdings (多账号持仓)
    ├── actual_trades (实盘记录，OCR 来源)
    ├── trade_outcomes (策略信号结果追踪)
    ├── strategy_performance (策略绩效聚合)
    └── trade_images (截图存储)

scan_reports / trade_plans (策略扫描层，与账号无关，保持全局)
```

---

## Phase 1: 多账号架构

### 1.1 accounts 表 (新建)

```sql
CREATE TABLE accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    broker VARCHAR(30) NOT NULL DEFAULT '',
    type VARCHAR(10) NOT NULL DEFAULT 'real',   -- real / sim
    initial_capital DECIMAL(14,2) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

初始化时插入默认账号 (id=1, 招商证券)，现有 holdings 数据自动归属。

### 1.2 已有表改造

```sql
-- holdings: 多账号持仓隔离
ALTER TABLE holdings
  ADD COLUMN account_id INT NOT NULL DEFAULT 1 AFTER id,
  ADD INDEX idx_account (account_id);

-- strategy_performance: 按账号统计，唯一键更新
ALTER TABLE strategy_performance
  ADD COLUMN account_id INT NOT NULL DEFAULT 1 AFTER id,
  DROP INDEX uk_mode_strategy_group,
  ADD UNIQUE KEY uk_account_strategy_group (account_id, strategy_id, signal_group);

-- trade_outcomes: 按账号追踪
ALTER TABLE trade_outcomes
  ADD COLUMN account_id INT NOT NULL DEFAULT 1 AFTER id,
  ADD INDEX idx_account (account_id);
```

scan_reports / trade_plans / backtest_* / price_snapshots **不改**。

### 1.3 Web 账号切换

- 顶部导航栏新增账号下拉选择器
- session 存储当前 account_id
- 各页面查询自动带 account_id 过滤
- `/holdings` `/performance` 路由按 account_id 加载

### 1.4 config.toml 变更

```toml
[[accounts]]
id = 1
name = "招商证券"
broker = "zhaoshang"

# 每个账号独立 portfolio
[accounts.portfolio]
available_cash = 20000
[[accounts.portfolio.holdings]]
symbol = "510300"
shares = 3000
cost_basis = 4.68
```

`config.py` 新增 `AccountSettings` dataclass，`AppConfig` 改 `portfolio: PortfolioSettings` 为 `accounts: list[AccountSettings]`。

---

## Phase 2A: 策略绩效追踪

### 2.1 strategy_performance 增强

在现有表上加列：

```sql
ALTER TABLE strategy_performance
  ADD COLUMN avg_holding_days DECIMAL(6,1) NOT NULL DEFAULT 0 AFTER avg_pnl_pct,
  ADD COLUMN max_drawdown_pct DECIMAL(8,2) NOT NULL DEFAULT 0 AFTER avg_holding_days,
  ADD COLUMN sharpe_ratio DECIMAL(6,2) DEFAULT NULL AFTER max_drawdown_pct,
  ADD COLUMN best_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0 AFTER sharpe_ratio,
  ADD COLUMN worst_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0 AFTER best_trade_pct,
  ADD COLUMN weekly_pnl_json TEXT AFTER worst_trade_pct,
  ADD COLUMN monthly_pnl_json TEXT AFTER weekly_pnl_json;
```

`weekly_pnl_json` 存近 4 周周盈亏 `[{"week":"2026-W17","pnl":820},...]`，`monthly_pnl_json` 存近 12 月。

### 2.2 trade_outcomes 生命周期

状态机：`pending` → `open` → `win` / `loss` / `expired`

```sql
ALTER TABLE trade_outcomes
  MODIFY COLUMN outcome VARCHAR(20) DEFAULT 'pending'
    COMMENT 'pending|open|win|loss|expired';
```

### 2.3 每日自动结算

在现有 `run_scheduled.py` 末尾新增 `update_strategy_performance()` 步骤：

1. 加载所有 `open` 状态的 trade_outcomes
2. 用当日收盘价检查止损/止盈
3. 触发的标记为 win/loss，记录 pnl
4. 重新计算 strategy_performance 全部指标
5. 更新 weekly/monthly PnL JSON

复用了 `risk.py` 的 `trailing_stop_exits()` 和 `trailing_take_profit_exits()` 逻辑。

### 2.4 绩效 Web 页面

路由 `/performance`（已有，增强）展示：
- 顶部：各策略累计 PnL 柱状图，每周 PnL 趋势折线
- 表格：strategy_id, 信号数, 胜率, 均盈%, 均亏%, 总PnL, 回撤, 夏普, 近4周明细
- 数据来源：`strategy_performance` 表

---

## Phase 2B: 实盘交易 OCR 记录

### 2B.1 新表

**trade_images** — 截图存储：

```sql
CREATE TABLE trade_images (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    file_path VARCHAR(500) NOT NULL,
    file_hash CHAR(32) NOT NULL,
    trade_date_hint DATE DEFAULT NULL,
    ocr_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    ocr_result_json TEXT,
    uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_file_hash (file_hash),
    INDEX idx_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**actual_trades** — 交易记录：

```sql
CREATE TABLE actual_trades (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL DEFAULT '',
    action VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    trade_time TIME DEFAULT NULL,
    price DECIMAL(10,3) NOT NULL DEFAULT 0,
    shares INT NOT NULL DEFAULT 0,
    amount DECIMAL(14,2) NOT NULL DEFAULT 0,
    commission DECIMAL(10,2) NOT NULL DEFAULT 0,
    image_id INT DEFAULT NULL,
    ocr_confidence DECIMAL(4,3) DEFAULT NULL,
    ocr_raw_text TEXT,
    source VARCHAR(20) NOT NULL DEFAULT 'ocr',
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (image_id) REFERENCES trade_images(id) ON DELETE SET NULL,
    INDEX idx_account_date (account_id, trade_date),
    INDEX idx_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2B.2 OCR 流水线

文件：`src/chaogu_alert/ocr.py` (新)

```
pipeline(image_path) → list[TradeDict]:
  1. PaddleOCR.ocr(image_path) → [{text, bbox, confidence}]
  2. _cluster_rows(text_boxes) → 按 y 坐标分组
  3. _extract_fields(row_texts) → 正则匹配:
     - 代码: \d{6}
     - 价格: \d+\.\d{3}
     - 方向: 买入|卖出|买|卖
     - 日期: \d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}
     - 时间: \d{2}:\d{2}:\d{2}
     - 数量: 整数，排除价格和代码
     - 金额: 含小数点的大数
  4. _validate(trade) → 校验字段完整性
  5. 返回结构化 list[dict]
```

### 2B.3 上传 API

`POST /trades/upload`:
- 接收图片文件 + account_id
- 计算 MD5 去重
- 保存到 `uploads/trades/{account_id}/{YYYY-MM}/`
- 写入 trade_images (ocr_status=pending)
- 异步/同步调用 OCR pipeline
- 返回 OCR 结果 JSON（含 confidence）

`POST /trades/confirm`:
- 接收修正后的交易列表 [{image_id, symbol, action, ...}]
- 写入 actual_trades
- 更新 trade_images.ocr_status = done

`POST /trades/manual`:
- 手动录入（无图片），source=manual

### 2B.4 交易记录 Web 页面

路由 `/trades` (新)：
- 按日期/标的筛选
- 表格展示：日期/标的/方向/价格/数量/金额/来源（OCR/手动）/缩略图链接
- 支持编辑/删除
- 上传区域：拖拽或点击上传截图

### 2B.5 PaddleOCR 依赖

`pyproject.toml` 新增 optional dependency:
```
[project.optional-dependencies]
ocr = ["paddleocr>=2.8", "paddlepaddle"]
```

---

## Phase 3: 对比分析页面

### 3.1 自动匹配逻辑

文件：`src/chaogu_alert/comparison.py` (新)

```python
def match_strategy_to_actual(
    trade_outcomes: list[dict],   # 策略信号 + 结果
    actual_trades: list[dict],    # OCR 实盘记录
    date_range: tuple[date, date],
) -> ComparisonResult:
```

匹配规则（同日期 + 同标的）：
| 条件 | 偏差类型 | 标签 |
|------|---------|------|
| 策略信号 + 实盘同日同向操作 | 完全一致 | match |
| 策略有买入信号 + 实盘无操作 | 漏单 | miss |
| 策略有止损卖出 + 实盘未卖出 | 不止损 | hold_loss |
| 策略无信号 + 实盘有买入 | 过度交易 | overtrade |
| 策略止盈未到 + 实盘已卖出 | 过早止盈 | early_exit |
| 实盘价偏离策略建议价 ±2% | 滑点 | slippage |

### 3.2 Web 页面

路由 `/compare` (新)：
- 顶部概览卡片：策略累计 PnL / 实盘累计 PnL / 执行差距 / 信号执行率
- PnL 双线叠加曲线（Chart.js 或纯 HTML 表格）
- 逐笔对比明细表（带偏差标签和颜色标记）
- 行为缺陷汇总表：各类型次数 + 累计损失

### 3.3 数据来源

- 策略信号：`trade_outcomes` 表
- 实盘记录：`actual_trades` 表
- 匹配 key：`(trade_date, symbol)` — 允许 ±1 天容差

---

## 实施顺序

| 阶段 | 内容 | 依赖 |
|------|------|------|
| Phase 1 | accounts 表 + account_id 外键 + 配置改造 | 无 |
| Phase 2A | strategy_performance 增强 + 每日结算 + Web 页面 | Phase 1 |
| Phase 2B | OCR 流水线 + actual_trades/trade_images 表 + 上传 API | Phase 1 |
| Phase 3 | 对比匹配引擎 + /compare 页面 | Phase 2A + 2B |

Phase 2A 和 2B 可并行开发。

---

## 关键约束

- PaddleOCR 首次加载模型约 30s，建议后台预热
- 截图存储在本地 `uploads/` 目录，需加入 `.gitignore`
- trade_outcomes 的 `open` 状态结算依赖每日扫描执行
- 多账号切换影响所有现有路由，需全面回归测试
- OCR 置信度 < 0.8 的记录必须人工确认
