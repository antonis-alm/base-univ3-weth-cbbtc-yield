from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from almanak.framework.market import MarketSnapshot, PriceUnavailableError, TokenBalance

from strategy import BaseUniv3WethCbbtcYieldStrategy


def _config(**overrides):
    base = {
        "chain": "base",
        "protocol": "uniswap_v3",
        "pool": "WETH/CBBTC/3000",
        "base_token": "WETH",
        "quote_token": "CBBTC",
        "entry": {"enabled": True, "min_idle_usd_to_deploy": 2500, "max_spread_bps": 40, "max_gas_usd": 8},
        "sizing": {"max_capital_deployed_pct": "85", "target_per_rebalance_pct": 25, "min_deploy_ticket_usd": 500},
        "risk": {
            "max_one_day_realized_volatility_pct": 7,
            "max_allowed_price_deviation_from_mid_bps": 180,
            "stop_loss_on_position_pct": 10,
            "max_drawdown_pct": "22",
            "min_health_buffer_pct": 30,
        },
        "il_hedging": {"enabled": True, "trigger_price_move_pct": 4, "hedge_ratio_pct": "0", "max_hedge_notional_pct": 40},
        "rebalancing": {
            "check_interval_minutes": 30,
            "rebalance_on_out_of_range": True,
            "range_width_pct": "5",
            "cooldown_minutes": 120,
            "max_rebalances_per_day": 6,
        },
        "fees": {"min_fee_claim_usd": 35, "reinvest_fees": True},
        "exit": {
            "take_profit_pct": 18,
            "time_stop_days": "30",
            "exit_on_apy_drop_below_pct": 12,
            "exit_on_tvl_drop_below_usd": 5000000,
            "teardown_policy": "RISK_OR_TIME",
        },
        "safety": {"slippage_bps": 70, "emergency_exit_on_liquidity_shock": True},
        "force_action": "",
    }
    base.update(overrides)
    return base


def _strategy(config=None):
    return BaseUniv3WethCbbtcYieldStrategy(
        config=config or _config(),
        chain="base",
        wallet_address="0x" + "1" * 40,
    )


def _market(
    weth_price: Decimal = Decimal("3500"),
    cbbtc_price: Decimal = Decimal("70000"),
    weth_usd: Decimal = Decimal("5000"),
    cbbtc_usd: Decimal = Decimal("5000"),
    spread_bps: Decimal = Decimal("10"),
    gas_usd: Decimal = Decimal("1"),
    daily_vol: Decimal = Decimal("0.03"),
    fee_apy: Decimal = Decimal("25"),
    tvl_usd: Decimal = Decimal("9000000"),
):
    market = MarketSnapshot(chain="base", wallet_address="0x" + "1" * 40)
    market.set_price("WETH", weth_price)
    market.set_price("CBBTC", cbbtc_price)

    weth_balance = weth_usd / weth_price
    cbbtc_balance = cbbtc_usd / cbbtc_price
    market.set_balance("WETH", TokenBalance(symbol="WETH", balance=weth_balance, balance_usd=weth_usd))
    market.set_balance("CBBTC", TokenBalance(symbol="CBBTC", balance=cbbtc_balance, balance_usd=cbbtc_usd))

    pair_price = weth_price / cbbtc_price
    pool_price = pair_price * (Decimal("1") + spread_bps / Decimal("10000"))
    market.pool_price_by_pair = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(price=pool_price)))
    market.estimate_swap_gas_cost_usd = MagicMock(return_value=gas_usd)
    market.realized_vol = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(daily=daily_vol)))
    market.best_pool = MagicMock(
        return_value=SimpleNamespace(
            data=SimpleNamespace(pool_address="0xpool", fee_apy=fee_apy, tvl_usd=tvl_usd)
        )
    )
    return market


def _intent_type(intent):
    return getattr(intent.intent_type, "value", str(intent.intent_type))


def test_entry_opens_lp_when_all_gates_pass():
    strategy = _strategy()
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "LP_OPEN"
    assert intent.pool == "WETH/CBBTC/3000"
    assert intent.protocol == "uniswap_v3"


def test_entry_holds_when_idle_capital_below_minimum():
    strategy = _strategy()
    market = _market(weth_usd=Decimal("500"), cbbtc_usd=Decimal("500"))

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"


def test_entry_holds_when_spread_above_limit():
    strategy = _strategy()
    market = _market(spread_bps=Decimal("55"))

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"


def test_open_position_closes_on_out_of_range_rebalance():
    strategy = _strategy()
    strategy._position_id = "123"
    strategy._range_lower = Decimal("0.047")
    strategy._range_upper = Decimal("0.048")
    strategy._last_rebalance_ts_iso = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "LP_CLOSE"
    assert intent.position_id == "123"


def test_open_position_holds_when_rebalance_cooldown_active():
    strategy = _strategy()
    strategy._position_id = "123"
    strategy._range_lower = Decimal("0.047")
    strategy._range_upper = Decimal("0.048")
    strategy._last_rebalance_ts_iso = datetime.now(UTC).isoformat()
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"


def test_open_position_closes_on_stop_loss():
    strategy = _strategy()
    strategy._position_id = "123"
    strategy._range_lower = Decimal("0.040")
    strategy._range_upper = Decimal("0.060")
    strategy._entry_mid_price = Decimal("0.06")
    strategy._entry_timestamp_iso = datetime.now(UTC).isoformat()
    strategy._entry_deployed_usd = Decimal("8000")
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "LP_CLOSE"


def test_open_position_closes_on_apy_drop():
    strategy = _strategy()
    strategy._position_id = "123"
    strategy._range_lower = Decimal("0.040")
    strategy._range_upper = Decimal("0.060")
    market = _market(fee_apy=Decimal("10"))

    intent = strategy.decide(market)

    assert _intent_type(intent) == "LP_CLOSE"


def test_force_action_open_returns_lp_open():
    strategy = _strategy(_config(force_action="open"))
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "LP_OPEN"


def test_force_action_swap_returns_swap():
    strategy = _strategy(_config(force_action="swap", force_swap_amount_usd=750))
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "SWAP"
    assert intent.amount_usd == Decimal("750")


def test_force_action_close_requires_position_id():
    strategy = _strategy(_config(force_action="close", force_position_id="999"))
    market = _market()

    intent = strategy.decide(market)

    assert _intent_type(intent) == "LP_CLOSE"
    assert intent.position_id == "999"


def test_data_unavailable_returns_hold():
    strategy = _strategy()
    market = _market()
    market.price = MagicMock(side_effect=PriceUnavailableError("no price"))

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"


def test_teardown_with_open_position_emits_lp_close():
    strategy = _strategy()
    strategy._position_id = "777"

    intents = strategy.generate_teardown_intents()

    assert len(intents) == 1
    assert _intent_type(intents[0]) == "LP_CLOSE"


def test_persistent_state_roundtrip():
    strategy = _strategy()
    strategy._position_id = "321"
    strategy._range_lower = Decimal("0.04")
    strategy._range_upper = Decimal("0.06")
    strategy._entry_timestamp_iso = datetime.now(UTC).isoformat()
    strategy._entry_mid_price = Decimal("0.05")
    strategy._entry_deployed_usd = Decimal("4200")
    strategy._last_rebalance_ts_iso = datetime.now(UTC).isoformat()
    strategy._rebalances_today = 3
    strategy._rebalance_day = datetime.now(UTC).date().isoformat()
    strategy._equity_peak_usd = Decimal("5000")
    strategy._cached_pool_address = "0xpool"
    strategy._last_known_tvl_usd = Decimal("8000000")
    strategy._last_known_apy_pct = Decimal("22")
    strategy._hedge_notional_usd = Decimal("250")

    saved = strategy.get_persistent_state()

    fresh = _strategy()
    fresh.load_persistent_state(saved)

    assert fresh.get_persistent_state() == saved
