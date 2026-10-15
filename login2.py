import asyncio, json, sys
sys.path.insert(0, "/srv/lunch"); import config
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
async def main():
    c = TelegramClient("/srv/lunch/user", config.TG_API_ID, config.TG_API_HASH)
    await c.connect()
    d = json.load(open("/srv/lunch/.login.json"))
    try:
        await c.sign_in(d["phone"], sys.argv[1], phone_code_hash=d["hash"])
    except SessionPasswordNeededError:
        if len(sys.argv) < 3: print("NEED_2FA_PASSWORD"); return
        await c.sign_in(password=sys.argv[2])
    me = await c.get_me(); print("OK", me.id, me.username)
    await c.disconnect()
asyncio.run(main())
