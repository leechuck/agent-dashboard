from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentdash.hub.terminals import router


def test_offline_terminal_explains_failure_before_closing():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(web_token='', hub_allowed_cidrs=['127.0.0.0/8'])
    app.state.hub = SimpleNamespace(
        db=SimpleNamespace(get_session=AsyncMock(return_value=None)),
        node_for=lambda key: None,
    )
    with TestClient(app, client=('127.0.0.1', 50000)).websocket_connect('/api/terminal/laptop:tmux:default:login:0.0') as ws:
        message = ws.receive_json()
        assert message['type'] == 'error'
        assert 'laptop is offline' in message['message']
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
