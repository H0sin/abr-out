from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bot.keyboards import BTN_BUY, BTN_SELL, BTN_WALLET, main_menu
from app.common.db.models import User
from app.common.db.session import SessionLocal

router = Router(name="menu")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        return
    async with SessionLocal() as session:
        stmt = (
            pg_insert(User)
            .values(telegram_id=message.from_user.id, username=message.from_user.username)
            .on_conflict_do_nothing(index_elements=["telegram_id"])
        )
        await session.execute(stmt)
        await session.commit()

    await message.answer(
        "سلام! به مارکت‌پلیس اوتباند خوش اومدی.\n\n"
        "از منوی پایین یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_BUY)
async def on_buy(message: Message) -> None:
    await message.answer("بخش خرید — به‌زودی فعال می‌شود.", reply_markup=main_menu())


@router.message(F.text == BTN_SELL)
async def on_sell(message: Message) -> None:
    await message.answer("بخش فروش — به‌زودی فعال می‌شود.", reply_markup=main_menu())


@router.message(F.text == BTN_WALLET)
async def on_wallet(message: Message) -> None:
    if message.from_user is None:
        return
    from app.common.db.wallet import get_balance

    async with SessionLocal() as session:
        balance = await get_balance(session, message.from_user.id)

    await message.answer(
        f"💵 موجودی کیف پول: <b>{balance:.4f} USD</b>",
        reply_markup=main_menu(),
    )
