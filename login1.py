import asyncio, json, sys
sys.path.insert(0, "/srv/lunch"); import config
from telethon import TelegramClient
async def main():
    c = TelegramClient("/srv/lunch/user", config.TG_API_ID, config.TG_API_HASH)
    await c.connect()
    if await c.is_user_authorized():
        me = await c.get_me(); print("ALREADY", me.id, me.username); return
    r = await c.send_code_request(sys.argv[1])
    json.dump({"phone": sys.argv[1], "hash": r.phone_code_hash}, open("/srv/lunch/.login.json", "w"))
    print("CODE_SENT", r.type.__class__.__name__)
    await c.disconnect()
asyncio.run(main())
