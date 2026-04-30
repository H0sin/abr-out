from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.menu import router as menu_router
from app.bot.handlers.topup import router as topup_router

__all__ = ["admin_router", "menu_router", "topup_router"]
