"""Independent same-origin Web control and signed LAN endpoints for private profile pairing."""
from __future__ import annotations

from http import HTTPStatus
import json
from urllib import parse

from . import web_server_impl14 as previous
from .cluster_private_profile_sync import ClusterPrivateProfileSync
from .diagnostics import problem

_PREVIOUS_MAKE_HANDLER = previous.make_handler

_PROFILE_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Леший — связать устройства</title>
<style>
:root{color-scheme:dark;font:16px system-ui,sans-serif;background:#111827;color:#f9fafb}
body{max-width:840px;margin:0 auto;padding:24px}
h1{font-size:1.65rem}h2{font-size:1.2rem;margin-top:28px}
p{color:#d1d5db;line-height:1.5}
input,button{font:inherit;padding:11px;border:1px solid #64748b;border-radius:8px;
background:#1f2937;color:#f9fafb;max-width:100%}
input{box-sizing:border-box;display:block;width:100%;margin:8px 0 12px}
button{cursor:pointer}button:disabled{opacity:.5}
.panel{border:1px solid #475569;border-radius:12px;padding:18px;margin:20px 0}
code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;padding:8px;background:#020617;border-radius:6px}
a{color:#93c5fd}#status{white-space:pre-wrap;overflow-wrap:anywhere}
</style></head><body>
<a href="/">← Вернуться к Лешему</a><h1>Синхронизация профиля между своими компьютерами</h1>
<p>Оба компьютера должны быть в одном доверенном LAN-кластере. Сопряжение подтверждается
на обоих устройствах, личные данные защищены отдельным ключом. Пока синхронизируются
только разрешённые настройки интерфейса; переписки, личная память, пароли и токены
не передаются.</p>
<div class="panel"><h2>1. Начать сопряжение на первом компьютере</h2>
<button id="invite">Создать одноразовый код</button>
<p>Адрес первого компьютера</p><code id="endpoint">—</code>
<p>ID приглашения</p><code id="invite-id">—</code>
<p>Секретный код (действует 5 минут, не публикуйте его)</p><code id="secret-code">—</code></div>
<div class="panel"><h2>2. Подключить второй компьютер</h2>
<label>LAN-адрес первого компьютера<input id="remote" placeholder="http://192.168.1.10:8765"></label>
<label>ID приглашения<input id="rid"></label>
<label>Секретный код<input id="code"></label>
<button id="join">Связать устройства</button></div>
<div class="panel"><h2>Активные соединения</h2><div id="pairs"></div>
<button id="refresh">Обновить статус</button></div>
<p id="status" role="status"></p>
<script>
(()=>{
const $=q=>document.querySelector(q), notify=s=>{$('#status').textContent=s};
async function api(path,body){
 const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json',
 'X-Leshy-UI':'private-profile'},body:JSON.stringify(body||{}),credentials:'same-origin',cache:'no-store'});
 const out=await res.json();if(!res.ok)throw Error(out.error||('HTTP '+res.status));return out;
}
async function refresh(){
 try{
 const res=await fetch('/v1/identity/cluster/status',{credentials:'same-origin',cache:'no-store'});
 const out=await res.json();if(!res.ok)throw Error(out.error||('HTTP '+res.status));
 const holder=$('#pairs');holder.replaceChildren();
 for(const item of out.pairs||[]){
  const p=document.createElement('p');p.append(document.createTextNode(item.remote_endpoint+' ('+item.remote_node_id.slice(0,12)+') '));
  const b=document.createElement('button');b.textContent='Отозвать доступ';
  b.onclick=async()=>{if(!confirm('Отключить это устройство?'))return;
   try{await api('/v1/identity/cluster/revoke',{pair_id:item.pair_id});notify('Привязка отозвана');refresh()}
   catch(e){notify(e.message)}};
  p.append(b);holder.append(p)
 }
 if(!(out.pairs||[]).length)holder.textContent='Сопряжённых устройств пока нет';
 }catch(e){notify(e.message)}
}
$('#invite').onclick=async()=>{try{
 const out=await api('/v1/identity/cluster/invite',{});
 $('#endpoint').textContent=out.endpoint;$('#invite-id').textContent=out.invite_id;
 $('#secret-code').textContent=out.code;notify('Код создан. Откройте эту страницу на втором устройстве.');
 }catch(e){notify(e.message)}};
$('#join').onclick=async()=>{try{
 await api('/v1/identity/cluster/join',{endpoint:$('#remote').value,invite_id:$('#rid').value,code:$('#code').value});
 $('#code').value='';notify('Устройства связаны. Настройки будут синхронизироваться автоматически.');refresh();
 }catch(e){notify(e.message)}};
$('#refresh').onclick=refresh;refresh();
})();
</script></body></html>"""


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    service = getattr(app, "cluster_private_profile_sync", None)
    if not isinstance(service, ClusterPrivateProfileSync):
        service = ClusterPrivateProfileSync(app, app.distributed_cluster)
        app.cluster_private_profile_sync = service
        old_stop = app.stop_background

        def stop_with_private_profile():
            service.stop()
            old_stop()

        app.stop_background = stop_with_private_profile
        service.start()

    class Handler(Parent):
        def _profile_owner(self):
            getter = getattr(self, "_existing_web_owner", None)
            owner = getter() if callable(getter) else ""
            if not owner or owner == "web:local":
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Сначала откройте главную страницу и войдите в свой Web-профиль"})
                return None
            return owner

        def _profile_request_allowed(self) -> bool:
            if self.headers.get("X-Leshy-UI") != "private-profile":
                return False
            origin = str(self.headers.get("Origin") or "")
            if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                return False
            return str(self.headers.get("Content-Type") or "").lower().startswith("application/json")

        def do_GET(self):
            path = parse.urlsplit(self.path).path
            if path == "/profile-sync":
                # A newly visiting browser gets its own HttpOnly identity cookie.
                try:
                    _owner, new_cookie, _hash = self._identity_context("web:local")
                except Exception as exc:
                    problem("cluster_private_profile_bootstrap_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Web-профиль недоступен"})
                raw = _PROFILE_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                cookie = self._identity_cookie_header(new_cookie)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(raw)
                return
            if path == "/v1/identity/cluster/status":
                owner = self._profile_owner()
                if owner is None:
                    return
                try:
                    return self._json(HTTPStatus.OK, service.status(owner))
                except Exception as exc:
                    problem("cluster_private_profile_status_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось прочитать привязки"})
            return super().do_GET()

        def do_POST(self):
            path = parse.urlsplit(self.path).path
            routes = {"/v1/cluster/private-profile/accept",
                      "/v1/cluster/private-profile/pull",
                      "/v1/cluster/private-profile/push"}
            if path in routes:
                if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Требуется JSON"})
                try:
                    body = self._body()
                    if len(json.dumps(body, ensure_ascii=False)) > 100_000:
                        raise ValueError("Слишком большой пакет")
                    if path.endswith("/accept"):
                        return self._json(HTTPStatus.OK, service.accept(body, self.headers))
                    kind = "pull" if path.endswith("/pull") else "push"
                    return self._json(HTTPStatus.OK, service.receive(kind, body, self.headers))
                except PermissionError:
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "Недостаточно доверия для синхронизации"})
                except (ValueError, TypeError):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Некорректное сообщение синхронизации"})
                except Exception as exc:
                    problem("cluster_private_profile_request_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Ошибка обмена профилем"})
            if path in {"/v1/identity/cluster/invite", "/v1/identity/cluster/join",
                        "/v1/identity/cluster/revoke"}:
                if not self._profile_request_allowed():
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "Требуется локальный запрос со страницы профиля"})
                owner = self._profile_owner()
                if owner is None:
                    return
                try:
                    body = self._body()
                    if path.endswith("/invite"):
                        return self._json(HTTPStatus.OK, service.invite(owner))
                    if path.endswith("/join"):
                        return self._json(HTTPStatus.OK, service.join(
                            owner, str(body.get("endpoint") or ""),
                            str(body.get("invite_id") or ""), str(body.get("code") or ""),
                        ))
                    return self._json(HTTPStatus.OK, service.revoke(owner, str(body.get("pair_id") or "")))
                except (ValueError, PermissionError, KeyError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("cluster_private_profile_web_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Операция не выполнена"})
            return super().do_POST()

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
