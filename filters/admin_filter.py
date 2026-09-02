"""فلتر التحقق من صلاحيات الأدمن اعتماداً على قاعدة البيانات."""

from aiogram.filters import BaseFilter


class IsAdmin(BaseFilter):
    """يتحقق من أن المستخدم أدمن عبر حقل is_admin في قاعدة البيانات."""

    async def __call__(self, event, db_user=None) -> bool:
        return bool(db_user and db_user.is_admin)
