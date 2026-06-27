import importlib
import sys
import types
from unittest.mock import MagicMock, patch, sentinel


def _import_ui_module():
    fake_templates = types.ModuleType("almanak.framework.dashboard.templates")
    fake_templates.get_uniswap_v3_config = MagicMock()
    fake_templates.prepare_lp_session_state = MagicMock()
    fake_templates.render_lp_dashboard = MagicMock()

    sys.modules.pop("dashboard.ui", None)
    with patch.dict(
        sys.modules,
        {
            "streamlit": MagicMock(),
            "almanak.framework.dashboard.templates": fake_templates,
        },
    ):
        return importlib.import_module("dashboard.ui")


def test_dashboard_module_imports():
    ui = _import_ui_module()

    assert callable(ui.render_custom_dashboard)


def test_render_custom_dashboard_builds_uniswap_template_config():
    ui = _import_ui_module()
    strategy_config = {
        "pool": "WETH/CBBTC/3000",
        "chain": "base",
        "protocol": "uniswap_v3",
    }

    with (
        patch.object(ui.st, "title") as title_mock,
        patch.object(ui, "get_uniswap_v3_config", return_value=sentinel.config) as get_config_mock,
        patch.object(ui, "prepare_lp_session_state", return_value={"position_id": "1"}) as prepare_mock,
        patch.object(ui, "render_lp_dashboard") as render_mock,
    ):
        ui.render_custom_dashboard(
            deployment_id="dep-123",
            strategy_config=strategy_config,
            api_client=sentinel.api_client,
            session_state={"custom": "value"},
        )

    title_mock.assert_called_once_with("Base Univ3 Weth Cbbtc Yield")
    get_config_mock.assert_called_once_with(
        token0="WETH",
        token1="CBBTC",
        fee_tier="0.30%",
        chain="base",
    )
    prepare_mock.assert_called_once_with(
        sentinel.api_client,
        session_state={"custom": "value"},
        config=sentinel.config,
        deployment_id="dep-123",
    )
    render_mock.assert_called_once_with(
        "dep-123",
        strategy_config,
        {"position_id": "1"},
        sentinel.config,
        api_client=sentinel.api_client,
    )
