import html
import os
import sqlite3
from contextlib import closing
from datetime import datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from aiogram import Bot

DB_PATH = os.getenv("DB_PATH", "market.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEB_USER = os.getenv("ADMIN_WEB_USER", "admin")
WEB_PASSWORD = os.getenv("ADMIN_WEB_PASSWORD", "change-me")

app = FastAPI(title="D&D Market Admin")
security = HTTPBasic()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def auth(credentials: HTTPBasicCredentials = Depends(security)):
    import secrets
    if not (secrets.compare_digest(credentials.username, WEB_USER) and secrets.compare_digest(credentials.password, WEB_PASSWORD)):
        from fastapi.responses import Response
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return True


def esc(v):
    return html.escape(str(v if v is not None else ""))


def page(body, title="D&D Market — Панель"):
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
body{{margin:0;background:#111;color:#eee;font-family:Inter,Arial,sans-serif}}a{{color:#f2c078;text-decoration:none}}.wrap{{max-width:1200px;margin:auto;padding:24px}}nav{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}}nav a,.btn{{background:#242424;color:#fff;border:1px solid #3a3a3a;border-radius:10px;padding:10px 14px;display:inline-block}}nav a:hover,.btn:hover{{background:#303030}}h1,h2{{margin-top:0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}.card{{background:#1b1b1b;border:1px solid #303030;border-radius:14px;padding:16px;margin-bottom:14px}}table{{width:100%;border-collapse:collapse;background:#1b1b1b;border-radius:12px;overflow:hidden}}th,td{{padding:11px;border-bottom:1px solid #303030;text-align:left;vertical-align:top}}th{{color:#bbb;font-size:13px}}input,textarea,select{{width:100%;box-sizing:border-box;background:#101010;color:#eee;border:1px solid #444;border-radius:9px;padding:10px;margin:6px 0 12px}}textarea{{min-height:110px}}form.inline{{display:inline}}.muted{{color:#999}}.ok{{color:#82d99b}}.warn{{color:#ffd27d}}.danger{{color:#ff8c8c}}.pill{{display:inline-block;padding:4px 8px;border-radius:99px;background:#2a2a2a;font-size:12px}}.actions{{display:flex;gap:8px;flex-wrap:wrap}}.message{{padding:10px 12px;border-radius:10px;background:#242424;margin:8px 0}}.user{{font-weight:700}}
</style></head><body><div class="wrap">
<h1>🐉 D&D Market — Admin</h1>
<nav><a href="/">Главная</a><a href="/tickets">🆘 Тикеты</a><a href="/users">👥 Пользователи</a><a href="/listings">📦 Товары</a></nav>
{body}
</div></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index(_: bool = Depends(auth)):
    con=db()
    users=con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    active=con.execute("SELECT COUNT(*) c FROM users WHERE last_seen_at >= datetime('now','-30 days')").fetchone()["c"]
    tickets=con.execute("SELECT COUNT(*) c FROM tickets WHERE status='open'").fetchone()["c"]
    listings=con.execute("SELECT COUNT(*) c FROM listings WHERE status='active'").fetchone()["c"]
    views=con.execute("SELECT COUNT(*) c FROM product_views").fetchone()["c"]
    con.close()
    body=f"""<div class='grid'>
<div class='card'><div class='muted'>Всего пользователей</div><h2>{users}</h2></div>
<div class='card'><div class='muted'>Активны за 30 дней</div><h2>{active}</h2></div>
<div class='card'><div class='muted'>Открытые тикеты</div><h2>{tickets}</h2></div>
<div class='card'><div class='muted'>Активные товары</div><h2>{listings}</h2></div>
<div class='card'><div class='muted'>Просмотры товаров</div><h2>{views}</h2></div></div>
<div class='card'><h2>Разделы</h2><div class='actions'><a class='btn' href='/tickets'>Открыть тикеты</a><a class='btn' href='/users'>Пользователи и действия</a><a class='btn' href='/listings'>Товары</a></div></div>"""
    return page(body)


@app.get("/tickets", response_class=HTMLResponse)
async def tickets(_: bool = Depends(auth)):
    con=db(); rows=con.execute("SELECT t.*,u.anon_name,u.username,u.tg_id FROM tickets t JOIN users u ON u.tg_id=t.user_id ORDER BY CASE WHEN t.status='open' THEN 0 ELSE 1 END,t.id DESC").fetchall(); con.close()
    cards=[]
    for r in rows:
        cards.append(f"""<div class='card'><div><span class='pill'>{esc(r['status'])}</span> <b>Тикет #{r['id']}</b></div>
<div class='muted'>Пользователь: {esc(r['anon_name'])} · ID {r['tg_id']} · @{esc(r['username'] or 'нет')}</div>
<p>{esc(r['text'])}</p><div class='actions'><a class='btn' href='/tickets/{r['id']}'>Открыть переписку</a>
<form class='inline' method='post' action='/tickets/{r['id']}/close'><button class='btn'>Закрыть</button></form>
<form class='inline' method='post' action='/tickets/{r['id']}/delete' onsubmit=\"return confirm('Удалить тикет?')\"><button class='btn'>Удалить</button></form></div></div>""")
    return page("<h2>🆘 Тикеты поддержки</h2>" + ("".join(cards) or "<div class='card'>Тикетов пока нет.</div>"))


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(ticket_id:int, _:bool=Depends(auth)):
    con=db(); t=con.execute("SELECT t.*,u.anon_name,u.username,u.tg_id FROM tickets t JOIN users u ON u.tg_id=t.user_id WHERE t.id=?",(ticket_id,)).fetchone(); msgs=con.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id",(ticket_id,)).fetchall() if t else [] ; con.close()
    if not t: raise HTTPException(404,"Ticket not found")
    messages="".join(f"<div class='message'><b>{'Пользователь' if m['sender_type']=='user' else 'Поддержка'}</b> <span class='muted'>{esc(m['created_at'])}</span><br>{esc(m['text'])}</div>" for m in msgs)
    body=f"""<div class='card'><h2>Тикет #{ticket_id}</h2><div>Пользователь: <b>{esc(t['anon_name'])}</b> · Telegram ID: <code>{t['tg_id']}</code> · @{esc(t['username'] or 'нет')}</div><div class='muted'>Статус: {esc(t['status'])}</div></div>
<div class='card'><h3>Переписка</h3>{messages or '<div class=muted>Сообщений нет</div>'}</div>
<div class='card'><h3>Ответ пользователю</h3><form method='post' action='/tickets/{ticket_id}/reply'><textarea name='text' placeholder='Сообщение будет отправлено пользователю в Telegram' required></textarea><button class='btn' type='submit'>Отправить</button></form></div>
<div class='actions'><form class='inline' method='post' action='/tickets/{ticket_id}/close'><button class='btn'>Закрыть тикет</button></form><form class='inline' method='post' action='/tickets/{ticket_id}/delete' onsubmit=\"return confirm('Удалить тикет?')\"><button class='btn'>Удалить тикет</button></form><a class='btn' href='/tickets'>Назад</a></div>"""
    return page(body, f"Тикет #{ticket_id}")


@app.post("/tickets/{ticket_id}/reply")
async def ticket_reply(ticket_id:int, text:str=Form(...), _:bool=Depends(auth)):
    con=db(); t=con.execute("SELECT * FROM tickets WHERE id=?",(ticket_id,)).fetchone()
    if not t: con.close(); raise HTTPException(404)
    text=text.strip()[:3000]
    if not text: con.close(); return RedirectResponse(f"/tickets/{ticket_id}",303)
    con.execute("INSERT INTO ticket_messages(ticket_id,sender_type,sender_id,text,created_at) VALUES(?,?,?,?,?)",(ticket_id,"admin",None,text,datetime.utcnow().isoformat(timespec='seconds'))); con.commit(); con.close()
    if BOT_TOKEN:
        try:
            async with Bot(BOT_TOKEN) as bot:
                await bot.send_message(t["user_id"], f"💬 <b>Ответ поддержки</b>\n\n{esc(text)}")
        except Exception:
            pass
    return RedirectResponse(f"/tickets/{ticket_id}",303)


@app.post("/tickets/{ticket_id}/close")
async def ticket_close(ticket_id:int, _:bool=Depends(auth)):
    con=db(); con.execute("UPDATE tickets SET status='closed',closed_at=? WHERE id=?",(datetime.utcnow().isoformat(timespec='seconds'),ticket_id)); con.commit(); con.close(); return RedirectResponse("/tickets",303)


@app.post("/tickets/{ticket_id}/delete")
async def ticket_delete(ticket_id:int, _:bool=Depends(auth)):
    con=db(); con.execute("DELETE FROM ticket_messages WHERE ticket_id=?",(ticket_id,)); con.execute("DELETE FROM tickets WHERE id=?",(ticket_id,)); con.commit(); con.close(); return RedirectResponse("/tickets",303)


@app.get("/users", response_class=HTMLResponse)
async def users(_:bool=Depends(auth)):
    con=db(); rows=con.execute("SELECT * FROM users ORDER BY COALESCE(last_seen_at,created_at) DESC").fetchall(); con.close()
    trs="".join(f"<tr><td><a href='/users/{r['tg_id']}'>{esc(r['anon_name'])}</a></td><td>{r['tg_id']}</td><td>{'@'+esc(r['username']) if r['username'] else '—'}</td><td>{'Продавец' if r['is_seller'] else 'Участник'}</td><td>{r['balance_rub']} ₽</td><td>⭐ {r['reputation']:.1f}</td><td>{esc(r['last_seen_at'] or r['created_at'])}</td></tr>" for r in rows)
    body=f"<h2>👥 Пользователи</h2><div class='card'><table><tr><th>Пользователь</th><th>Telegram ID</th><th>Username</th><th>Роль</th><th>Баланс</th><th>Репутация</th><th>Последняя активность</th></tr>{trs}</table></div>"
    return page(body)


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(user_id:int, _:bool=Depends(auth)):
    con=db(); u=con.execute("SELECT * FROM users WHERE tg_id=?",(user_id,)).fetchone()
    if not u: con.close(); raise HTTPException(404)
    actions=con.execute("SELECT * FROM user_actions WHERE user_id=? ORDER BY id DESC LIMIT 100",(user_id,)).fetchall()
    views=con.execute("SELECT pv.created_at,l.id,l.title FROM product_views pv JOIN listings l ON l.id=pv.listing_id WHERE pv.user_id=? ORDER BY pv.id DESC LIMIT 100",(user_id,)).fetchall()
    topups=con.execute("SELECT * FROM balance_topups WHERE user_id=? ORDER BY id DESC",(user_id,)).fetchall()
    purchases=con.execute("SELECT p.*,l.title FROM purchases p LEFT JOIN listings l ON l.id=p.listing_id WHERE p.buyer_id=? ORDER BY p.id DESC",(user_id,)).fetchall()
    con.close()
    acts="".join(f"<tr><td>{esc(a['created_at'])}</td><td>{esc(a['action'])}</td><td>{esc(a['target_type'])} #{esc(a['target_id'])}</td><td>{esc(a['details'])}</td></tr>" for a in actions) or "<tr><td colspan=4>Нет действий</td></tr>"
    vws="".join(f"<tr><td>{esc(v['created_at'])}</td><td>#{v['id']}</td><td>{esc(v['title'])}</td></tr>" for v in views) or "<tr><td colspan=3>Нет просмотров</td></tr>"
    tps="".join(f"<tr><td>{esc(t['created_at'])}</td><td>{t['rub_amount']} ₽</td><td>{t['stars_amount']} ⭐</td></tr>" for t in topups) or "<tr><td colspan=3>Нет пополнений</td></tr>"
    pur="".join(f"<tr><td>{esc(p['created_at'])}</td><td>#{p['listing_id']}</td><td>{esc(p['title'])}</td><td>{p['stars']} ⭐</td></tr>" for p in purchases) or "<tr><td colspan=4>Нет покупок</td></tr>"
    body=f"""<div class='card'><h2>👤 {esc(u['anon_name'])}</h2><p>Telegram ID: <code>{u['tg_id']}</code><br>Username: @{esc(u['username'] or 'нет')}<br>Роль: {'Продавец' if u['is_seller'] else 'Участник'}<br>⭐ Репутация: {u['reputation']:.1f}/5.0 ({u['reputation_votes']})<br>Создан: {esc(u['created_at'])}<br>Последняя активность: {esc(u['last_seen_at'] or '—')}</p>
<form method='post' action='/users/{user_id}/balance'><label>Изменить баланс, ₽</label><input type='number' name='amount' required><button class='btn'>Применить изменение</button></form></div>
<div class='card'><h3>📋 Действия</h3><table><tr><th>Время</th><th>Действие</th><th>Объект</th><th>Детали</th></tr>{acts}</table></div>
<div class='card'><h3>👁 Просмотренные товары</h3><table><tr><th>Время</th><th>ID</th><th>Товар</th></tr>{vws}</table></div>
<div class='card'><h3>💰 История пополнений</h3><table><tr><th>Время</th><th>Рубли</th><th>Stars</th></tr>{tps}</table></div>
<div class='card'><h3>🛒 Покупки</h3><table><tr><th>Время</th><th>ID</th><th>Товар</th><th>Stars</th></tr>{pur}</table></div>
<a class='btn' href='/users'>Назад к пользователям</a>"""
    return page(body, f"Пользователь {user_id}")


@app.post("/users/{user_id}/balance")
async def user_balance(user_id:int, amount:int=Form(...), _:bool=Depends(auth)):
    con=db(); con.execute("UPDATE users SET balance_rub=balance_rub+? WHERE tg_id=?",(amount,user_id)); con.commit(); con.close(); return RedirectResponse(f"/users/{user_id}",303)


@app.get("/listings", response_class=HTMLResponse)
async def listings_admin(_:bool=Depends(auth)):
    con=db(); rows=con.execute("SELECT l.*,COALESCE(u.seller_name,'Администрация') seller FROM listings l LEFT JOIN users u ON u.tg_id=l.seller_id ORDER BY l.id DESC").fetchall(); con.close()
    trs="".join(f"<tr><td>#{r['id']}</td><td>{esc(r['title'])}</td><td>{esc(r['seller'])}</td><td>{r['price_stars']} ⭐ / {r['price_rub']} ₽</td><td>{'✅' if r['verified'] else '—'}</td><td>{esc(r['status'])}</td></tr>" for r in rows)
    return page(f"<h2>📦 Товары</h2><div class='card'><table><tr><th>ID</th><th>Название</th><th>Продавец</th><th>Цена</th><th>Проверен</th><th>Статус</th></tr>{trs}</table></div>")
