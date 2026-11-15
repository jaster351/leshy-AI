from __future__ import annotations

"""Human-in-the-loop search challenge page over the existing HTTP server."""

from http import HTTPStatus
import os
from urllib import parse

from . import web_server_impl12 as previous
from .search_challenge_handoff import BROKER
from .search_challenge_telegram import send_challenge_photo
from .diagnostics import problem

_PREVIOUS_MAKE_HANDLER = previous.make_handler

_CHALLENGE_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Леший · Подтверждение поиска</title>
<style>
:root{color-scheme:dark;font:16px system-ui,-apple-system,sans-serif;background:#101827;color:#f1f5f9}
body{max-width:850px;margin:0 auto;padding:16px}h1{font-size:1.45rem}
p{color:#cbd5e1;line-height:1.5}#shot{display:block;width:100%;height:auto;cursor:crosshair;border:1px solid #64748b;border-radius:8px;touch-action:manipulation}
button,input{font:inherit;border-radius:8px;padding:11px;background:#1e293b;color:white;border:1px solid #64748b}
button{cursor:pointer}button:disabled{opacity:.5}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
input{min-width:150px;flex:1}#results a{color:#93c5fd}
small{color:#94a3b8}
</style></head><body>
<h1>Подтверждение поиска Лешего</h1>
<p id="status" role="status">Подключение к активной браузерной сессии…</p>
<p>Нажимайте на изображение там, где требуется действие. Для ввода текста сначала выберите поле на изображении. Браузер и cookies остаются на устройстве Лешего.</p>
<img id="shot" alt="Живой снимок страницы проверки" style="display:none">
<div class="controls">
<input id="answer" autocomplete="off" placeholder="Текст ответа, если нужен" maxlength="256">
<button id="type">Ввести</button><button id="enter">Enter</button>
<button id="check">Проверить</button><button id="refresh">Обновить страницу</button>
<button id="cancel">Отмена</button>
</div>
<div id="results"></div>
<small>Доступ по временной секретной ссылке. Не пересылайте её посторонним.</small>
<script>
(()=>{
const ticket=decodeURIComponent(location.hash.slice(1));
const status=document.querySelector('#status'),shot=document.querySelector('#shot');
const buttons=[...document.querySelectorAll('button')];
let busy=false, live=true, lastImage='';
async function post(path,body) {
 const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,ticket}),cache:'no-store',credentials:'omit'});
 if(!res.ok)throw new Error('HTTP '+res.status);
 return res.json();
}
async function update(){
 if(!ticket){status.textContent='Отсутствует код проверки в ссылке';live=false;return;}
 try {
 const s=await post('/v1/browser-challenge/status',{});
 status.textContent=(s.message||s.status)+' · '+s.engine+' · '+s.expires_in_seconds+' с';
 if(s.screenshot && lastImage!==s.screenshot){shot.src=s.screenshot;lastImage=s.screenshot;shot.style.display='block';}
 if(s.status!=='pending'){
   live=false;buttons.forEach(b=>b.disabled=true);shot.style.display='none';
   const list=document.querySelector('#results');list.replaceChildren();
   for(const item of (s.results||[])){const p=document.createElement('p'),a=document.createElement('a');a.href=item.url;a.textContent=item.title;a.target='_blank';a.rel='noopener noreferrer';p.append(a);list.append(p);}
 }
 }catch(e){status.textContent='Не удалось получить состояние проверки: '+e.message;}
 if(live)setTimeout(update,1500);
}
async function act(action,extra={}){if(busy||!live)return;busy=true;try{await post('/v1/browser-challenge/action',{action,...extra});}catch(e){status.textContent='Ошибка действия: '+e.message;}finally{busy=false;}}
shot.addEventListener('click',e=>{
 const r=shot.getBoundingClientRect();
 const x=Math.round((e.clientX-r.left)*shot.naturalWidth/r.width);
 const y=Math.round((e.clientY-r.top)*shot.naturalHeight/r.height);
 if(Number.isFinite(x)&&Number.isFinite(y))act('click',{x,y});
});
document.querySelector('#type').onclick=()=>act('type',{text:document.querySelector('#answer').value});
document.querySelector('#enter').onclick=()=>act('press',{text:'Enter'});
document.querySelector('#check').onclick=()=>act('check');
document.querySelector('#refresh').onclick=()=>act('refresh');
document.querySelector('#cancel').onclick=()=>{if(confirm('Отменить проверку?'))act('cancel');};
update();
})();
</script></body></html>'''


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)

    def notify(ticket):
        # Only an explicitly configured Telegram owner receives the secret.
        access = telegram._access()
        chat_id = str(access.get("owner_id") or "").strip()
        if not chat_id.isdigit():
            return
        base = os.getenv("LESHY_PUBLIC_URL", "").strip().rstrip("/")
        route = "/browser-challenge#" + ticket.ticket
        where = base + route if base.startswith(("http://", "https://")) else route
        sent_photo = False
        try:
            send_challenge_photo(telegram, chat_id, ticket.screenshot, ticket.engine, where)
            sent_photo = True
        except Exception as exc:
            # Never include the secret ticket, bot token or screenshot in logs.
            problem("search_captcha_telegram_photo_failed", type(exc).__name__)
        if not sent_photo or not where.startswith("https://"):
            # Text fallback when photo API fails, or when the admin has not
            # configured a public HTTPS URL for the inline keyboard button.
            telegram._send_answer(
                chat_id,
                "Для поискового браузера Лешего требуется подтверждение.\n"
                "Поисковик: " + ticket.engine + "\n"
                "Откройте страницу проверки: " + where + "\n"
                "После решения Леший автоматически продолжит исходный поиск. "
                "Ссылка действительна 10 минут.",
            )

    BROKER.set_notifier(notify)

    class Handler(Parent):
        def do_GET(self):
            if parse.urlsplit(self.path).path == "/browser-challenge":
                raw = _CHALLENGE_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(raw)
                return
            return super().do_GET()

        def do_POST(self):
            path = parse.urlsplit(self.path).path
            if path not in {"/v1/browser-challenge/status", "/v1/browser-challenge/action"}:
                return super().do_POST()
            # The high-entropy capability ticket is the sole credential for
            # these two tightly scoped endpoints; a Telegram link can open on
            # mobile without copying the application's global API token.
            try:
                body = self._body()
                token = str(body.get("ticket") or "")
                if len(token) < 30 or len(token) > 128:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Проверка не найдена"})
                record = BROKER.get(token)
                if record is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Проверка не найдена"})
                if path.endswith("/status"):
                    return self._json(HTTPStatus.OK, record)
                action = str(body.get("action") or "")
                try:
                    x = float(body.get("x") or 0)
                    y = float(body.get("y") or 0)
                except (ValueError, TypeError):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Некорректные координаты"})
                ok = BROKER.enqueue(token, action, x=x, y=y, text=str(body.get("text") or ""))
                return self._json(HTTPStatus.ACCEPTED if ok else HTTPStatus.CONFLICT, {"accepted": ok})
            except (TypeError, ValueError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    original = previous.make_handler
    previous.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        previous.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
