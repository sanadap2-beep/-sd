"""Keyboards for the agent admin panel."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def agent_admin_list_kb(agents, revoked) -> InlineKeyboardMarkup:
    """لوحة إدارة الوكلاء: إنشاء/أكواد + قائمة الوكلاء الفعّل والمسخوبين."""
    b = InlineKeyboardMarkup(inline_keyboard=[])
    rows = b.inline_keyboard
    rows.append(
        [
            InlineKeyboardButton(text="🔑 إنشاء كود وكالة", callback_data="admin:agent_create", style="primary"),
            InlineKeyboardButton(text="📋 الأكواد المتوفرة", callback_data="admin:agent_codes", style="primary"),
        ]
    )
    for profile in agents[:30]:
        user = profile.user
        name = (user.full_name or f"#{user.telegram_id}") if user else f"#{profile.user_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🟢 {name} · خصم {profile.percent}%",
                    callback_data=f"admin:agent_detail:{profile.user_id}", style="primary",
                )
            ]
        )
    for profile in revoked[:10]:
        user = profile.user
        name = (user.full_name or f"#{user.telegram_id}") if user else f"#{profile.user_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔴 {name} (مسلوب)",
                    callback_data=f"admin:agent_detail:{profile.user_id}", style="primary",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:main")])
    return b


def agent_admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 رجوع لإدارة الوكلاء", callback_data="admin:agents")]
        ]
    )


def agent_codes_kb(codes) -> InlineKeyboardMarkup:
    rows = []
    for code in codes:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🚫 إلغاء {code.code[-5:]}",
                    callback_data=f"admin:agent_void:{code.id}", style="danger",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:agents")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agent_detail_kb(profile) -> InlineKeyboardMarkup:
    uid = profile.user_id
    rows = [[InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:agents")]]
    if profile.status == "active":
        rows = [
            [
                InlineKeyboardButton(
                    text="➕ زيادة نسبة الخصم (1%)",
                    callback_data=f"admin:agent_pct:{uid}:+1", style="primary",
                ),
                InlineKeyboardButton(
                    text="➖ تقليل نسبة الخصم (1%)",
                    callback_data=f"admin:agent_pct:{uid}:-1", style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 إلغاء الوكالة", callback_data=f"admin:agent_revoke:{uid}", 
                style="danger")
            ],
            rows[0],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
