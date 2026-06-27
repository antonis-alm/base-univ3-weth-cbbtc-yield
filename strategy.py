import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from almanak.framework.intents import Intent
from almanak.framework.market import (
    GasUnavailableError,
    MarketSnapshot,
    PoolAnalyticsUnavailableError,
    PoolPriceUnavailableError,
    PriceUnavailableError,
    VolatilityUnavailableError,
)
from almanak.framework.strategies import IntentStrategy, almanak_strategy

logger = logging.getLogger(__name__)


def _safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Enum):
        return getattr(v, "value", str(v))
    return v


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


@almanak_strategy(
    name="base_univ3_weth_cbbtc_yield",
    description="Concentrated Uniswap V3 LP strategy for WETH/CBBTC on Base",
    version="1.0.0",
    author="Generated",
    tags=["generated", "dynamic_lp", "uniswap_v3", "base"],
    supported_chains=["base"],
    supported_protocols=["uniswap_v3"],
    intent_types=["LP_OPEN", "LP_CLOSE", "SWAP", "HOLD"],
    default_chain="base",
    quote_asset="USD",
)
class BaseUniv3WethCbbtcYieldStrategy(IntentStrategy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def get_config(key: str, default: Any) -> Any:
            if isinstance(self.config, dict):
                return self.config.get(key, default)
            return getattr(self.config, key, default)

        self.config_chain = get_config("chain", self.chain)
        self.execution_chain = str(self.config_chain or self.chain)
        self.pool = get_config("pool", "WETH/CBBTC/3000")
        self.protocol = get_config("protocol", "uniswap_v3")
        self.base_token = get_config("base_token", "WETH")
        self.quote_token = get_config("quote_token", "CBBTC")

        entry = get_config("entry", {})
        sizing = get_config("sizing", {})
        risk = get_config("risk", {})
        il_hedging = get_config("il_hedging", {})
        rebalancing = get_config("rebalancing", {})
        fees = get_config("fees", {})
        exit_cfg = get_config("exit", {})
        safety = get_config("safety", {})

        self.entry_min_idle_usd = _decimal(entry.get("min_idle_usd_to_deploy", 2500))
        self.entry_max_spread_bps = _decimal(entry.get("max_spread_bps", 40))
        self.entry_max_gas_usd = _decimal(entry.get("max_gas_usd", 8))

        self.max_capital_deployed_pct = _decimal(sizing.get("max_capital_deployed_pct", 85))
        self.target_per_rebalance_pct = _decimal(sizing.get("target_per_rebalance_pct", 25))
        self.min_deploy_ticket_usd = _decimal(sizing.get("min_deploy_ticket_usd", 500))

        self.max_one_day_realized_volatility_pct = _decimal(risk.get("max_one_day_realized_volatility_pct", 7))
        self.max_allowed_price_deviation_from_mid_bps = _decimal(risk.get("max_allowed_price_deviation_from_mid_bps", 180))
        self.stop_loss_on_position_pct = _decimal(risk.get("stop_loss_on_position_pct", 10))
        self.max_drawdown_pct = _decimal(risk.get("max_drawdown_pct", 22))
        self.min_health_buffer_pct = _decimal(risk.get("min_health_buffer_pct", 30))

        self.il_hedging_enabled = bool(il_hedging.get("enabled", False))
        self.il_trigger_price_move_pct = _decimal(il_hedging.get("trigger_price_move_pct", 4))
        self.hedge_ratio_pct = _decimal(il_hedging.get("hedge_ratio_pct", 0))
        self.max_hedge_notional_pct = _decimal(il_hedging.get("max_hedge_notional_pct", 40))

        self.rebalance_on_out_of_range = bool(rebalancing.get("rebalance_on_out_of_range", True))
        self.range_width_pct = _decimal(rebalancing.get("range_width_pct", 5))
        self.cooldown_minutes = int(rebalancing.get("cooldown_minutes", 120))
        self.max_rebalances_per_day = int(rebalancing.get("max_rebalances_per_day", 6))

        self.min_fee_claim_usd = _decimal(fees.get("min_fee_claim_usd", 35))
        self.reinvest_fees = bool(fees.get("reinvest_fees", True))

        self.take_profit_pct = _decimal(exit_cfg.get("take_profit_pct", 18))
        self.time_stop_days = _decimal(exit_cfg.get("time_stop_days", 30))
        self.exit_on_apy_drop_below_pct = _decimal(exit_cfg.get("exit_on_apy_drop_below_pct", 12))
        self.exit_on_tvl_drop_below_usd = _decimal(exit_cfg.get("exit_on_tvl_drop_below_usd", 5_000_000))
        self.teardown_policy = str(exit_cfg.get("teardown_policy", "RISK_OR_TIME"))

        self.slippage_bps = _decimal(safety.get("slippage_bps", 70))
        self.emergency_exit_on_liquidity_shock = bool(safety.get("emergency_exit_on_liquidity_shock", True))

        self.force_action = str(get_config("force_action", "") or "").strip().lower()
        self.force_position_id = get_config("force_position_id", None)
        self.force_swap_direction = str(get_config("force_swap_direction", "weth_to_cbbtc") or "weth_to_cbbtc").strip().lower()
        self.force_swap_amount_usd = _decimal(get_config("force_swap_amount_usd", 500))

        self._position_id: str | None = None
        self._range_lower: Decimal | None = None
        self._range_upper: Decimal | None = None
        self._entry_timestamp_iso: str | None = None
        self._entry_mid_price: Decimal | None = None
        self._entry_deployed_usd: Decimal = Decimal("0")
        self._last_rebalance_ts_iso: str | None = None
        self._rebalances_today: int = 0
        self._rebalance_day: str | None = None
        self._equity_peak_usd: Decimal = Decimal("0")
        self._cached_pool_address: str | None = None
        self._last_known_tvl_usd: Decimal | None = None
        self._last_known_apy_pct: Decimal | None = None
        self._hedge_notional_usd: Decimal = Decimal("0")
        self._last_pair_price: Decimal | None = None

    def decide(self, market: MarketSnapshot) -> Optional[Intent]:
        if self.force_action:
            return self._forced_intent(market)

        try:
            context = self._read_market_context(market)
        except (
            PriceUnavailableError,
            PoolPriceUnavailableError,
            PoolAnalyticsUnavailableError,
            VolatilityUnavailableError,
            GasUnavailableError,
            ValueError,
            KeyError,
        ) as exc:
            return Intent.hold(reason=f"data unavailable: {exc}")

        self._last_pair_price = context["pair_price"]

        if self._position_id is not None:
            return self._decide_with_position(market, context)
        return self._decide_without_position(market, context)

    def _read_market_context(self, market: MarketSnapshot) -> dict[str, Decimal | None | str]:
        base_price = _decimal(market.price(self.base_token))
        quote_price = _decimal(market.price(self.quote_token))
        if base_price <= 0 or quote_price <= 0:
            raise ValueError("invalid token price")
        pair_price = base_price / quote_price

        pool_price = pair_price
        try:
            pool_price_env = market.pool_price_by_pair(
                self.base_token,
                self.quote_token,
                chain=self.execution_chain,
                protocol=self.protocol,
                fee_tier=3000,
            )
            pool_price_data = getattr(pool_price_env, "data", pool_price_env)
            pool_price = _decimal(getattr(pool_price_data, "price", pool_price))
        except (PoolPriceUnavailableError, ValueError):
            pool_price = pair_price

        spread_bps = Decimal("0")
        if pair_price > 0:
            spread_bps = abs(pool_price - pair_price) / pair_price * Decimal("10000")

        volatility_pct = None
        try:
            vol_env = market.realized_vol(self.base_token, window_days=1, timeframe="1h")
            vol_data = getattr(vol_env, "data", vol_env)
            if hasattr(vol_data, "daily"):
                volatility_pct = _decimal(getattr(vol_data, "daily")) * Decimal("100")
            elif hasattr(vol_data, "daily_vol"):
                volatility_pct = _decimal(getattr(vol_data, "daily_vol")) * Decimal("100")
            elif hasattr(vol_data, "annualized"):
                volatility_pct = _decimal(getattr(vol_data, "annualized")) / Decimal("19.1049731745") * Decimal("100")
        except (VolatilityUnavailableError, ValueError):
            volatility_pct = None

        gas_cost_usd = Decimal("0")
        try:
            gas_cost_usd = _decimal(market.estimate_swap_gas_cost_usd(chain=self.execution_chain))
        except (GasUnavailableError, ValueError):
            gas_cost_usd = Decimal("0")

        apy_pct: Decimal | None = self._last_known_apy_pct
        tvl_usd: Decimal | None = self._last_known_tvl_usd
        pool_address = self._cached_pool_address
        try:
            analytics_env = market.best_pool(
                self.base_token,
                self.quote_token,
                chain=self.execution_chain,
                metric="fee_apy",
                protocols=[self.protocol],
            )
            analytics = getattr(analytics_env, "data", analytics_env)
            pool_address = str(getattr(analytics, "pool_address", pool_address or "")) or pool_address
            apy_raw = getattr(analytics, "fee_apy", apy_pct)
            tvl_raw = getattr(analytics, "tvl_usd", tvl_usd)
            apy_pct = _decimal(apy_raw) if apy_raw is not None else apy_pct
            tvl_usd = _decimal(tvl_raw) if tvl_raw is not None else tvl_usd
        except (PoolAnalyticsUnavailableError, ValueError):
            pass

        if pool_address:
            self._cached_pool_address = pool_address
        if apy_pct is not None:
            self._last_known_apy_pct = apy_pct
        if tvl_usd is not None:
            self._last_known_tvl_usd = tvl_usd

        return {
            "base_price": base_price,
            "quote_price": quote_price,
            "pair_price": pair_price,
            "spread_bps": spread_bps,
            "volatility_pct": volatility_pct,
            "gas_cost_usd": gas_cost_usd,
            "apy_pct": apy_pct,
            "tvl_usd": tvl_usd,
            "pool_address": pool_address,
        }

    def _decide_without_position(self, market: MarketSnapshot, context: dict[str, Any]) -> Intent:
        risk_hold = self._risk_gate_hold_reason(context)
        if risk_hold:
            return Intent.hold(reason=risk_hold)

        base_balance = market.balance(self.base_token)
        quote_balance = market.balance(self.quote_token)
        base_usd = _decimal(base_balance.balance_usd)
        quote_usd = _decimal(quote_balance.balance_usd)
        total_idle_usd = base_usd + quote_usd

        if total_idle_usd < self.entry_min_idle_usd:
            return Intent.hold(reason="idle capital below entry minimum")

        deployable_usd = total_idle_usd * self.max_capital_deployed_pct / Decimal("100")
        reserve_floor_pct = max(Decimal("100") - self.max_capital_deployed_pct, self.min_health_buffer_pct)
        reserve_usd = total_idle_usd * reserve_floor_pct / Decimal("100")
        deployable_usd = min(deployable_usd, total_idle_usd - reserve_usd)

        if deployable_usd < self.min_deploy_ticket_usd:
            return Intent.hold(reason="deployable ticket below minimum")

        skew_usd = abs(base_usd - quote_usd)
        rebalance_cap_usd = deployable_usd * self.target_per_rebalance_pct / Decimal("100")
        if skew_usd > deployable_usd * Decimal("0.20"):
            swap_amount = min(skew_usd / Decimal("2"), rebalance_cap_usd)
            if swap_amount > 0:
                if base_usd > quote_usd:
                    return self._swap_intent(self.base_token, self.quote_token, swap_amount)
                return self._swap_intent(self.quote_token, self.base_token, swap_amount)

        deploy_per_side_usd = deployable_usd / Decimal("2")
        amount0 = deploy_per_side_usd / context["base_price"]
        amount1 = deploy_per_side_usd / context["quote_price"]
        if amount0 <= 0 or amount1 <= 0:
            return Intent.hold(reason="insufficient balances to open LP")

        range_lower, range_upper = self._compute_range(context["pair_price"])
        self._entry_deployed_usd = deployable_usd
        return Intent.lp_open(
            pool=self.pool,
            amount0=amount0,
            amount1=amount1,
            range_lower=range_lower,
            range_upper=range_upper,
            protocol=self.protocol,
            chain=self.execution_chain,
        )

    def _decide_with_position(self, market: MarketSnapshot, context: dict[str, Any]) -> Intent:
        risk_hold = self._risk_gate_hold_reason(context)
        if risk_hold and self._position_id:
            return self._close_position_intent(self._position_id)

        age_days_dec = Decimal("0")
        if self._entry_timestamp_iso:
            age_days = (datetime.now(UTC) - datetime.fromisoformat(self._entry_timestamp_iso)).total_seconds() / 86400
            age_days_dec = Decimal(str(age_days))

        risk_exit = False
        time_exit = age_days_dec >= self.time_stop_days

        if self._entry_mid_price and self._entry_mid_price > 0:
            change_pct = (context["pair_price"] - self._entry_mid_price) / self._entry_mid_price * Decimal("100")
            risk_exit = change_pct <= -self.stop_loss_on_position_pct or change_pct >= self.take_profit_pct

            equity = self._entry_deployed_usd * (Decimal("1") + change_pct / Decimal("100"))
            self._equity_peak_usd = max(self._equity_peak_usd, equity)
            if self._equity_peak_usd > 0:
                drawdown_pct = (self._equity_peak_usd - equity) / self._equity_peak_usd * Decimal("100")
                if drawdown_pct >= self.max_drawdown_pct:
                    risk_exit = True

            if self.il_hedging_enabled and self.hedge_ratio_pct > 0:
                move_pct = abs(change_pct)
                if move_pct >= self.il_trigger_price_move_pct:
                    hedge_cap = self._entry_deployed_usd * self.max_hedge_notional_pct / Decimal("100")
                    target_hedge = self._entry_deployed_usd * self.hedge_ratio_pct / Decimal("100")
                    available = max(Decimal("0"), min(target_hedge, hedge_cap) - self._hedge_notional_usd)
                    if available > 0 and context["gas_cost_usd"] <= self.entry_max_gas_usd:
                        if change_pct > 0:
                            return self._swap_intent(self.base_token, self.quote_token, available)
                        return self._swap_intent(self.quote_token, self.base_token, available)

            if self.reinvest_fees and self._entry_deployed_usd > 0 and age_days_dec > 0:
                apy = context.get("apy_pct") or Decimal("0")
                estimated_fees = self._entry_deployed_usd * apy / Decimal("100") * age_days_dec / Decimal("365")
                if estimated_fees >= self.min_fee_claim_usd:
                    return self._close_position_intent(self._position_id)

        should_close = risk_exit
        if self.teardown_policy == "RISK_OR_TIME":
            should_close = risk_exit or time_exit
        elif self.teardown_policy == "TIME_ONLY":
            should_close = time_exit

        if should_close:
            return self._close_position_intent(self._position_id)

        if self.rebalance_on_out_of_range and self._range_lower is not None and self._range_upper is not None:
            if context["pair_price"] < self._range_lower or context["pair_price"] > self._range_upper:
                if not self._rebalance_allowed_now():
                    return Intent.hold(reason="rebalance blocked by cooldown or daily limit")
                self._mark_rebalance_requested()
                return self._close_position_intent(self._position_id)

        return Intent.hold(reason="position healthy; waiting")

    def _risk_gate_hold_reason(self, context: dict[str, Any]) -> str | None:
        if context["spread_bps"] > self.entry_max_spread_bps:
            return "spread above configured max"
        if context["spread_bps"] > self.max_allowed_price_deviation_from_mid_bps:
            return "price deviation above configured max"
        if context["gas_cost_usd"] > self.entry_max_gas_usd:
            return "gas above configured max"
        vol_pct = context.get("volatility_pct")
        if vol_pct is not None and vol_pct > self.max_one_day_realized_volatility_pct:
            return "realized volatility above configured max"

        apy_pct = context.get("apy_pct")
        if apy_pct is not None and apy_pct < self.exit_on_apy_drop_below_pct:
            return "pool APY below configured floor"

        tvl_usd = context.get("tvl_usd")
        if tvl_usd is not None and tvl_usd < self.exit_on_tvl_drop_below_usd:
            return "pool TVL below configured floor"

        if self.emergency_exit_on_liquidity_shock and self._liquidity_shock_detected(tvl_usd):
            return "emergency liquidity shock detected"

        return None

    def _liquidity_shock_detected(self, current_tvl: Decimal | None) -> bool:
        if current_tvl is None or self._last_known_tvl_usd is None or self._last_known_tvl_usd <= 0:
            return False
        drop_pct = (self._last_known_tvl_usd - current_tvl) / self._last_known_tvl_usd * Decimal("100")
        return drop_pct >= Decimal("30")

    def _rebalance_allowed_now(self) -> bool:
        today = datetime.now(UTC).date().isoformat()
        if self._rebalance_day != today:
            self._rebalance_day = today
            self._rebalances_today = 0

        if self._rebalances_today >= self.max_rebalances_per_day:
            return False

        if not self._last_rebalance_ts_iso:
            return True

        last_rebalance = datetime.fromisoformat(self._last_rebalance_ts_iso)
        return datetime.now(UTC) - last_rebalance >= timedelta(minutes=self.cooldown_minutes)

    def _mark_rebalance_requested(self) -> None:
        today = datetime.now(UTC).date().isoformat()
        if self._rebalance_day != today:
            self._rebalance_day = today
            self._rebalances_today = 0
        self._rebalances_today += 1
        self._last_rebalance_ts_iso = datetime.now(UTC).isoformat()

    def _forced_intent(self, market: MarketSnapshot) -> Intent:
        if self.force_action == "open":
            pair_price = _decimal(market.price(self.base_token)) / _decimal(market.price(self.quote_token))
            range_lower, range_upper = self._compute_range(pair_price)
            return Intent.lp_open(
                pool=self.pool,
                amount0=Decimal("0.1"),
                amount1=Decimal("0.001"),
                range_lower=range_lower,
                range_upper=range_upper,
                protocol=self.protocol,
                chain=self.execution_chain,
            )

        if self.force_action == "swap":
            if self.force_swap_direction == "cbbtc_to_weth":
                return self._swap_intent(self.quote_token, self.base_token, self.force_swap_amount_usd)
            return self._swap_intent(self.base_token, self.quote_token, self.force_swap_amount_usd)

        if self.force_action == "close":
            position_id = str(self.force_position_id or self._position_id or "")
            if not position_id:
                return Intent.hold(reason="force close requested without position id")
            return self._close_position_intent(position_id)

        return Intent.hold(reason=f"unsupported force_action: {self.force_action}")

    def _compute_range(self, pair_price: Decimal) -> tuple[Decimal, Decimal]:
        width = self.range_width_pct / Decimal("100")
        half_width = width / Decimal("2")
        lower = pair_price * (Decimal("1") - half_width)
        upper = pair_price * (Decimal("1") + half_width)
        return lower, upper

    def _swap_intent(self, from_token: str, to_token: str, amount_usd: Decimal) -> Intent:
        return Intent.swap(
            from_token=from_token,
            to_token=to_token,
            amount_usd=amount_usd,
            max_slippage=self.slippage_bps / Decimal("10000"),
            protocol=self.protocol,
            chain=self.execution_chain,
        )

    def _close_position_intent(self, position_id: str) -> Intent:
        return Intent.lp_close(
            position_id=str(position_id),
            pool=self.pool,
            collect_fees=True,
            protocol=self.protocol,
            chain=self.execution_chain,
        )

    def get_status(self) -> dict[str, Any]:
        return {
            "strategy": "base_univ3_weth_cbbtc_yield",
            "chain": self.execution_chain,
            "wallet": self.wallet_address[:10] + "..." if self.wallet_address else None,
            "state": "open" if self._position_id else "idle",
            "position_id": self._position_id,
            "range_lower": _safe(self._range_lower),
            "range_upper": _safe(self._range_upper),
            "entry_mid_price": _safe(self._entry_mid_price),
            "entry_deployed_usd": _safe(self._entry_deployed_usd),
            "rebalances_today": self._rebalances_today,
            "last_known_tvl_usd": _safe(self._last_known_tvl_usd),
            "last_known_apy_pct": _safe(self._last_known_apy_pct),
        }

    def on_intent_executed(self, intent, success: bool, result):
        if not success:
            return

        intent_type = getattr(getattr(intent, "intent_type", None), "value", "")
        if intent_type == "LP_OPEN":
            position_id = getattr(result, "position_id", None)
            self._position_id = str(position_id) if position_id is not None else self._position_id
            rl = getattr(intent, "range_lower", None)
            ru = getattr(intent, "range_upper", None)
            self._range_lower = _decimal(rl) if rl is not None else self._range_lower
            self._range_upper = _decimal(ru) if ru is not None else self._range_upper
            self._entry_timestamp_iso = datetime.now(UTC).isoformat()
            if self._last_pair_price is not None:
                self._entry_mid_price = self._last_pair_price
            self._equity_peak_usd = max(self._equity_peak_usd, self._entry_deployed_usd)

        if intent_type == "LP_CLOSE":
            self._position_id = None
            self._range_lower = None
            self._range_upper = None
            self._entry_timestamp_iso = None
            self._entry_mid_price = None
            self._entry_deployed_usd = Decimal("0")
            self._hedge_notional_usd = Decimal("0")

        if intent_type == "SWAP":
            self._hedge_notional_usd += _decimal(getattr(intent, "amount_usd", Decimal("0")))

    def get_persistent_state(self):
        return {
            "position_id": self._position_id,
            "range_lower": str(self._range_lower) if self._range_lower is not None else None,
            "range_upper": str(self._range_upper) if self._range_upper is not None else None,
            "entry_timestamp_iso": self._entry_timestamp_iso,
            "entry_mid_price": str(self._entry_mid_price) if self._entry_mid_price is not None else None,
            "entry_deployed_usd": str(self._entry_deployed_usd),
            "last_rebalance_ts_iso": self._last_rebalance_ts_iso,
            "rebalances_today": self._rebalances_today,
            "rebalance_day": self._rebalance_day,
            "equity_peak_usd": str(self._equity_peak_usd),
            "cached_pool_address": self._cached_pool_address,
            "last_known_tvl_usd": str(self._last_known_tvl_usd) if self._last_known_tvl_usd is not None else None,
            "last_known_apy_pct": str(self._last_known_apy_pct) if self._last_known_apy_pct is not None else None,
            "hedge_notional_usd": str(self._hedge_notional_usd),
        }

    def load_persistent_state(self, state):
        if not state:
            return
        pid = state.get("position_id")
        self._position_id = str(pid) if pid is not None else None
        self._range_lower = _decimal(state["range_lower"]) if state.get("range_lower") else None
        self._range_upper = _decimal(state["range_upper"]) if state.get("range_upper") else None
        self._entry_timestamp_iso = state.get("entry_timestamp_iso")
        self._entry_mid_price = _decimal(state["entry_mid_price"]) if state.get("entry_mid_price") else None
        self._entry_deployed_usd = _decimal(state.get("entry_deployed_usd", "0"))
        self._last_rebalance_ts_iso = state.get("last_rebalance_ts_iso")
        self._rebalances_today = int(state.get("rebalances_today", 0))
        self._rebalance_day = state.get("rebalance_day")
        self._equity_peak_usd = _decimal(state.get("equity_peak_usd", "0"))
        self._cached_pool_address = state.get("cached_pool_address")
        self._last_known_tvl_usd = _decimal(state["last_known_tvl_usd"]) if state.get("last_known_tvl_usd") else None
        self._last_known_apy_pct = _decimal(state["last_known_apy_pct"]) if state.get("last_known_apy_pct") else None
        self._hedge_notional_usd = _decimal(state.get("hedge_notional_usd", "0"))

    def get_open_positions(self):
        from datetime import datetime

        from almanak.framework.teardown import PositionInfo, PositionType, TeardownPositionSummary

        positions = []
        if self._position_id:
            positions.append(
                PositionInfo(
                    position_type=PositionType.LP,
                    position_id=str(self._position_id),
                    chain=self.execution_chain,
                    protocol=self.protocol,
                    value_usd=self._entry_deployed_usd,
                    details={
                        "pool": self.pool,
                        "range_lower": str(self._range_lower) if self._range_lower is not None else None,
                        "range_upper": str(self._range_upper) if self._range_upper is not None else None,
                    },
                )
            )

        return TeardownPositionSummary(
            deployment_id=getattr(self, "deployment_id", "base_univ3_weth_cbbtc_yield"),
            timestamp=datetime.now(UTC),
            positions=positions,
        )

    def generate_teardown_intents(self, mode=None, market=None) -> list[Intent]:
        if not self._position_id:
            return []

        return [self._close_position_intent(self._position_id)]
