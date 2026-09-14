from aiogram import Router

from . import admin, build, dice, landlord, misc, mortgage, newgame, purchase, replies, swap, votes

router = Router(name="root")
router.include_router(newgame.router)
router.include_router(replies.router)
router.include_router(dice.router)
router.include_router(purchase.router)
router.include_router(landlord.router)
router.include_router(votes.router)
router.include_router(build.router)
router.include_router(mortgage.router)
router.include_router(swap.router)
router.include_router(admin.router)
router.include_router(misc.router)

__all__ = ["router"]
