from __future__ import annotations

from html import escape

from .config import AppConfig
from .models import ScanReport, TradePlan
from .strategies import get_strategy_definition, iter_strategy_definitions

ACTION_LABELS = {
    "buy": "买入",
    "sell": "卖出",
}

GROUP_LABELS = {
    "open": "开仓信号",
    "t_trade": "做T信号",
}

MARKET_REGIME_LABELS = {
    "risk_on": "偏强，适合优先关注趋势和轮动机会",
    "risk_off": "偏弱，建议降低追高意愿并控制仓位",
    "unknown": "暂时无法判断，建议按保守方式处理",
}


def build_subject(config: AppConfig, report: ScanReport) -> str:
    open_count = len([plan for plan in report.trade_plans if plan.signal_group == "open"])
    t_count = len([plan for plan in report.trade_plans if plan.signal_group == "t_trade"])
    return (
        f"{config.email.subject_prefix} "
        f"{report.as_of.isoformat()} "
        f"开仓 {open_count} / 做T {t_count}"
    )


def render_text(config: AppConfig, report: ScanReport) -> str:
    open_plans = [plan for plan in report.trade_plans if plan.signal_group == "open"]
    t_plans = [plan for plan in report.trade_plans if plan.signal_group == "t_trade"]

    lines = [
        "A股 ETF 策略提醒",
        f"日期：{report.as_of.isoformat()}",
        f"市场状态：{_market_regime_text(report.market_regime)}",
        f"基准标的：{report.benchmark_symbol}",
        f"候选信号数：{len(report.candidates)}",
        f"开仓建议数：{len(open_plans)}",
        f"做T建议数：{len(t_plans)}",
        f"过滤标的数：{report.filtered_count}",
        "",
        "阅读提示：",
        "- 开仓信号用于新开仓或顺势加仓。",
        "- 做T信号只对已持仓标的有意义，用来做日内低吸高抛。",
        "",
        "策略说明：",
    ]

    lines.extend(_render_strategy_guides())
    lines.extend(
        [
            "",
            "本次策略执行概览：",
        ]
    )
    lines.extend(_render_strategy_runs(report))
    lines.extend(
        [
            "",
            "资金与风控：",
            f"- 总资金：{config.risk.capital:,.0f} 元",
            f"- 现金缓冲：{config.risk.cash_buffer_pct:.0%}",
            f"- 最多持仓数：{config.risk.max_positions}",
            f"- 单笔风险预算：{config.risk.risk_per_trade_pct:.0%}",
            "",
        ]
    )

    lines.extend(_render_section("开仓信号", open_plans))
    lines.append("")
    lines.extend(_render_section("做T信号", t_plans))
    return "\n".join(lines).strip()


def render_html(config: AppConfig, report: ScanReport) -> str:
    open_plans = [plan for plan in report.trade_plans if plan.signal_group == "open"]
    t_plans = [plan for plan in report.trade_plans if plan.signal_group == "t_trade"]
    open_rows = "".join(_render_html_row(plan) for plan in open_plans)
    t_rows = "".join(_render_html_row(plan) for plan in t_plans)

    return f"""
<html>
  <body style="font-family:'Microsoft YaHei','PingFang SC','Segoe UI',sans-serif;color:#222;line-height:1.6;background:#f7f8fa;padding:20px;">
    <div style="max-width:1120px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;">
      <h2 style="margin:0 0 12px 0;font-size:24px;">A股 ETF 策略提醒</h2>
      <p style="margin:0 0 16px 0;color:#4b5563;">
        日期：{escape(report.as_of.isoformat())}<br/>
        市场状态：{escape(_market_regime_text(report.market_regime))}<br/>
        基准标的：{escape(report.benchmark_symbol)}<br/>
        候选信号数：{len(report.candidates)}，开仓建议数：{len(open_plans)}，做T建议数：{len(t_plans)}
      </p>
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:18px;">
        <strong>阅读提示</strong>
        <ul style="margin:8px 0 0 18px;padding:0;">
          <li>开仓信号用于新开仓或顺势加仓。</li>
          <li>做T信号只针对已持仓标的，用来做日内低吸高抛。</li>
        </ul>
      </div>
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:18px;">
        <strong>策略说明</strong>
        <ul style="margin:8px 0 0 18px;padding:0;">
          {_render_html_strategy_guides()}
        </ul>
      </div>
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:18px;">
        <strong>本次策略执行概览</strong>
        <ul style="margin:8px 0 0 18px;padding:0;">
          {_render_html_strategy_runs(report)}
        </ul>
      </div>
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:18px;">
        <strong>资金与风控</strong>
        <ul style="margin:8px 0 0 18px;padding:0;">
          <li>总资金：{config.risk.capital:,.0f} 元</li>
          <li>现金缓冲：{config.risk.cash_buffer_pct:.0%}</li>
          <li>最多持仓数：{config.risk.max_positions}</li>
          <li>单笔风险预算：{config.risk.risk_per_trade_pct:.0%}</li>
        </ul>
      </div>
      <h3 style="margin:22px 0 10px 0;">开仓信号</h3>
      {_render_html_table(open_rows)}
      <h3 style="margin:22px 0 10px 0;">做T信号</h3>
      {_render_html_table(t_rows)}
    </div>
  </body>
</html>
""".strip()


def _render_strategy_guides() -> list[str]:
    return [
        f"- {definition.display_name}：{definition.description}"
        for definition in iter_strategy_definitions()
    ]


def _render_html_strategy_guides() -> str:
    return "".join(
        f"<li><strong>{escape(definition.display_name)}</strong>：{escape(definition.description)}</li>"
        for definition in iter_strategy_definitions()
    )


def _render_strategy_runs(report: ScanReport) -> list[str]:
    return [
        f"- {run.display_name}：覆盖 {len(run.symbol_scope)} 个标的，产出 {run.signal_count} 个信号"
        for run in report.strategy_runs
    ]


def _render_html_strategy_runs(report: ScanReport) -> str:
    return "".join(
        f"<li><strong>{escape(run.display_name)}</strong>：覆盖 {len(run.symbol_scope)} 个标的，产出 {run.signal_count} 个信号</li>"
        for run in report.strategy_runs
    )


def _render_section(title: str, plans: list[TradePlan]) -> list[str]:
    lines = [f"{title}："]
    if not plans:
        lines.append("- 暂无信号")
        return lines

    for index, plan in enumerate(plans, start=1):
        lines.extend(_render_plan(index, plan))
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return lines


def _render_plan(index: int, plan: TradePlan) -> list[str]:
    reasons = "；".join(_localize_reason(reason) for reason in plan.reasons)
    return [
        f"{index}. {plan.symbol} {plan.name} | {_action_label(plan)} | {_strategy_label(plan.strategy_id)}",
        f"   建议说明：{_plan_summary(plan)}",
        f"   参考价格：参考价 {plan.entry_price:.3f}，止损价 {plan.stop_loss:.3f}，止盈参考 {plan.take_profit:.3f}",
        f"   建议仓位：{plan.suggested_shares} 股，预计金额 {plan.suggested_value:,.2f} 元，占总资金 {plan.position_pct:.2%}，预计风险金额 {plan.risk_amount:,.2f} 元",
        f"   触发原因：{reasons}",
    ]


def _render_html_table(rows: str) -> str:
    if not rows:
        rows = (
            "<tr><td colspan='10' style='padding:12px;border:1px solid #ddd;text-align:center;color:#6b7280;'>暂无信号</td></tr>"
        )
    return (
        "<table style='border-collapse:collapse;width:100%;margin-bottom:20px;font-size:14px;'>"
        "<thead><tr style='background:#f3f4f6;'>"
        "<th style='border:1px solid #ddd;padding:8px;'>操作建议</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>策略名称</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>标的</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>参考价</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>止损价</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>止盈参考</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>建议股数</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>预计金额</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>资金占比</th>"
        "<th style='border:1px solid #ddd;padding:8px;'>触发原因</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def _render_html_row(plan: TradePlan) -> str:
    reasons = "<br/>".join(escape(_localize_reason(reason)) for reason in plan.reasons)
    return (
        "<tr>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{escape(_action_label(plan))}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'><strong>{escape(_strategy_label(plan.strategy_id))}</strong><br/><span style='color:#6b7280;'>{escape(_plan_summary(plan))}</span></td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{escape(plan.symbol)} {escape(plan.name)}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{plan.entry_price:.3f}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{plan.stop_loss:.3f}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{plan.take_profit:.3f}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{plan.suggested_shares}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{plan.suggested_value:,.2f} 元</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{plan.position_pct:.2%}</td>"
        f"<td style='border:1px solid #ddd;padding:8px;vertical-align:top;'>{reasons}</td>"
        "</tr>"
    )


def _strategy_label(strategy_id: str) -> str:
    definition = get_strategy_definition(strategy_id)
    return definition.display_name if definition else strategy_id


def _action_label(plan: TradePlan) -> str:
    action = ACTION_LABELS.get(plan.action, plan.action)
    group = GROUP_LABELS.get(plan.signal_group, plan.signal_group)
    return f"{group}{action}"


def _plan_summary(plan: TradePlan) -> str:
    if plan.signal_group == "open":
        if plan.action == "buy":
            return "适合新开仓或顺势加仓，优先在趋势确认后分批介入。"
        return "适合作为开仓阶段的卖出参考。"
    if plan.signal_group == "t_trade":
        if plan.action == "buy":
            return "适合对已有持仓做低吸T，前提是你当前持有该标的。"
        return "适合对已有持仓做高抛T，不是清仓信号。"
    return "请结合仓位和风险承受能力自行判断。"


def _market_regime_text(regime: str) -> str:
    return MARKET_REGIME_LABELS.get(regime, regime)


def _localize_reason(reason: str) -> str:
    if reason.startswith("close ") and " > MA" in reason:
        return reason.replace("close ", "收盘价 ").replace(" > ", "，高于 ")
    if reason.startswith("20d return "):
        return reason.replace("20d return ", "近20日涨幅 ")
    if reason.startswith("volume ratio "):
        return reason.replace("volume ratio ", "量能比值 ")
    if reason == "20-day breakout":
        return "突破近20日高点"
    if reason.startswith("relative strength vs benchmark "):
        return reason.replace("relative strength vs benchmark ", "相对基准强度 ")
    if reason == "benchmark regime risk_on":
        return "当前市场环境偏强"
    if reason.startswith("rotation rank #"):
        return reason.replace("rotation rank #", "轮动强度排名第 ")
    if reason.startswith("holding shares "):
        return reason.replace("holding shares ", "当前持仓 ") + " 股"
    if reason.startswith("intraday pullback "):
        return reason.replace("intraday pullback ", "日内回撤 ")
    if reason.startswith("intraday rebound "):
        return reason.replace("intraday rebound ", "日内反弹 ")
    if reason.startswith("price below VWAP by "):
        return reason.replace("price below VWAP by ", "价格低于均价线(VWAP) ")
    if reason.startswith("price above VWAP by "):
        return reason.replace("price above VWAP by ", "价格高于均价线(VWAP) ")
    if reason.startswith("trend MA") and reason.endswith(" still intact"):
        return reason.replace(" still intact", " 趋势仍保持完好")
    if reason.startswith("intraday volume "):
        return reason.replace("intraday volume ", "日内成交量 ")
    if reason.startswith("t size "):
        return reason.replace("t size ", "建议按当前持仓的 ").replace(" of current holding", " 做T")
    return reason
