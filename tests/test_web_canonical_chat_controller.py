from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Regression воспроизводит пользовательский сценарий: первый ответ завершён,
# следующий вопрос в той же Web-сессии обязан начать новый request немедленно.


def test_canonical_controller_is_loaded_last_and_old_finalizer_is_not_injected():
    server = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
    assert '<script src="/chat-controller-canonical.js"></script>' in server
    assert "html = html.replace('<script src=\"/chat-followup-finalizer.js\"></script>\\n', \"\")" in server
    assert "web_chat_stream_received" in server
    assert "web_chat_stream_http_finished" in server


def test_done_releases_active_request_before_transport_finishes():
    js = (ROOT / "web" / "chat-controller-canonical.js").read_text(encoding="utf-8")
    done = js.index("}else if(type==='done'){")
    release = js.index("if(active===req)active=null;", done)
    cancel = js.index("reader.cancel('terminal SSE received')", done)
    assert release < cancel
    assert "req.terminal=true" in js[done:cancel]
    assert "window.sendFrom=send" in js
    assert "event.stopImmediatePropagation();send(target);" in js


def test_each_send_gets_new_request_id_and_current_session():
    js = (ROOT / "web" / "chat-controller-canonical.js").read_text(encoding="utf-8")
    assert "requestId:rid()" in js
    assert "sessionId:sid()" in js
    assert "session_id:req.sessionId" in js
    assert "request_id:req.requestId" in js


def test_thinking_state_does_not_create_fake_answer_card_before_first_delta():
    js = (ROOT / "web" / "chat-controller-canonical.js").read_text(encoding="utf-8")
    request = js.index("const req={requestId:rid()")
    first_delta = js.index("}else if(type==='delta'){")
    assert "ui:null" in js[request:first_delta]
    assert "ui:makeBox()" not in js[request:first_delta]
    assert "state('Леший думает…')" in js[request:first_delta]
    assert "function ensureBox(req)" in js
    assert "if(!req.ui||!req.ui.box||!req.ui.box.isConnected)req.ui=makeBox();" in js
    # Потоковая карточка создаётся лениво из render(), то есть только после delta.
    render = js.index("function render(req)")
    assert "const ui=ensureBox(req);" in js[render:first_delta]
