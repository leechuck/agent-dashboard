from agentdash.hub.clientguard import client_allowed


def test_loopback_and_tailnet_allowed():
    assert client_allowed("127.0.0.1")
    assert client_allowed("100.84.109.108")
    assert client_allowed("fd7a:115c:a1e0::4401:53bd")


def test_lan_and_garbage_denied():
    assert not client_allowed("10.73.11.5")
    assert not client_allowed("8.8.8.8")
    assert not client_allowed("")
    assert not client_allowed("not-an-ip")
