"""إعادة تصنيف خدمات Hyper Store إلى الأقسام الصحيحة + ترجمة أسماء الألعاب."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("BOT_USERNAME", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ADMIN_NOTIFY_CHAT_ID", "-1")
os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db"))
os.environ.setdefault("INVENTORY_ENCRYPTION_KEY", "dev")

from sqlalchemy import select, update
from database.seed import init_db
from database.engine import async_session_maker
from database.models import (
    ApiProvider, Category, CategoryType, SubCategory, Product, ProductStatus,
    ProviderService,
)

# ── خريطة التصنيف: فئة المزود → نوع قسم البوت ──
CATEGORY_MAP = {
    # الألعاب الحقيقية
    "games": ["PUBG", "Free Fire", "Mobile Legend", "Mobile Legends", "Jawaker", "Blood strike",
              "Blood Strike", "8Ball Pool", "Delta force", "Age Of Empires", "Acecraft", "Arena Breakout",
              "Black Clover", "Bullet Echo", "City Of Crime", "Coin Ball", "Cash Ball", "Crystal of Atlan",
              "Devil May Cry", "FarLight84", "Genshen Impact", "Guns of glory", "Hero clash", "Honkai",
              "Honor of king", "King Shot", "Lords mobile", "Ludo clud", "Marvel Reveals", "Oxide",
              "Project entropy", "Stumble Guys", "Super SUS", "Whiteout Survival", "Yalla Ludo", "Zepeto",
              "afk journey", "age of magic", "arena of valor", "arknights", "asphalt", "astral guardians",
              "badlanders", "ballistic hero", "be the king", "blade x", "blockman go", "captain tsubasa",
              "civilization eras", "clash of plants", "cloud song", "crossfire legend", "crossout mobile",
              "crystalfall", "deadly dudes", "division resurgence", "dragon nest", "dragon raja",
              "dragonheir", "dream and lethe", "duet night", "dynasty heroes", "echocalypse", "eggy party",
              "enhypen world", "ensemble stars", "eve echoes", "extraordinary ones", "football master",
              "garena speed", "gearup booster", "ghost story", "goddess of victory", "golden spatula",
              "growtopia", "haikyu fly", "hatsune miku", "heartopia", "heaven burns", "hyper front",
              "identity v", "idol party", "infinite lagrange", "isekai feast", "jade dynasty",
              "journey renewed", "kings choice", "kuroko street", "legacy of discord", "legend of the phoenix",
              "life makeover", "love and deepspace", "love nikki", "magic chess", "marvel duel",
              "marvel mystic", "mirage perfect", "modern strike", "mongil star", "my singing monsters",
              "onmyoji arena", "overmortal idle", "stormshot", "Punishing", "Revenge of Sultans",
              "Brawl", "eFootball", "FarLight", "Free Fire ID", "Free Fire memberships", "Jawaker Accelerators",
              "Jawaker packages", "PUBG Memberships", "PUBG New State", "Brawl Pass", "Pro Pass",
              "Blizard", "Steam"],
    # تطبيقات لايف/دردشة
    "apps": ["MIXU", "YALLA LIVE", "MOMO LIVE", "التطبيقات", "gearup", "In video", "Watch tv", "Bbb"],
    # أرصدة الهاتف
    "balances": ["MTN", "SYRIATEL", "Alfa", "Touch", "Alfa", "Zain", "الارقام", "قسم الأرصدة",
                  "مزود MTS", "مزود إلكم", "مزود امنية", "مزود امواج", "مزود اية", "مزود اينت",
                  "مزود برو", "مزود بطاقات", "مزود دنيا", "مزود سما", "مزود سوا", "مزود ليزر",
                  "مزود لينت", "مزود هاي فاي", "مزود هايبر", "مزود يارا", "Touch", "Zain"],
    # بطاقات ورصيد ألعاب
    "cards": ["PlayStation", "Steam Wallet", "Xbox", "iTunes", "Apple TV", "Amazon Prime",
              "ITUNES", "AMAZON", "Google", "غوغل", "PlayStation", "RAZER", "Steam",
              "AMAZON SAUDI", "AMAZON TRY", "AMAZON UAE", "AMAZON USA", "خدمات الدفع السورية",
              "خدمات الشام كاش", "شامنا", "شاهد VIP", "IP TV", "Spotify", "NETFLIX",
              "Apple Music", "YouTube Premium", "Canva pro", "PicsArt", "Blue 4k", "OSN Plus",
              "LOOK TV", "Watch tv", "IP TV", "In video", "عملات بيس", "العملات الرقمية",
              "USDT", "تبادل رقمي", "اكس بوكس", "تذكرة الحدث", "التذكرة الذهبية",
              "اشتراكات -", "اشتراكات", "Telegram Premium", "حسابات جاهزة", "نجوم تلجرام"],
    # أكواد رقمية
    "codes": [],
    # اشتراكات رقمية
    "subscriptions": [],
}

# ترجمة أسماء الألعاب الشائعة للعربية
AR_NAMES = {
    "PUBG": "ببجي موبايل", "Free Fire": "فري فاير", "Mobile Legend": "موبايل ليجندز",
    "Mobile Legend Global": "موبايل ليجندز", "Mobile Legends Turkish": "موبايل ليجندز تركي",
    "Jawaker": "جواكر", "Blood Strike": "بلود سترايك", "Blood strike": "بلود سترايك",
    "8Ball Pool": "8Ball Pool", "Delta force": "ديلتا فورس", "Age Of Empires": "عصر الإمبراطوريات",
    "Acecraft": "أيسكرافت", "Arena Breakout": "أرينا بريك آوت", "Black Clover": "بلاك كلوفر",
    "Bullet Echo": "بوليت إيكو", "City Of Crime": "مدينة الجريمة", "Coin Ball": "كوين بول",
    "Cash Ball": "كاش بول", "Crystal of Atlan": "كريستال أتلان", "Devil May Cry": "ديفيل ماي كراي",
    "FarLight84": "فارلايت 84", "Genshen Impact": "جنشن إمباكت", "Guns of glory": "غانز أوف غلوري",
    "Hero clash": "هيرو كلاش", "Honkai": "هونكاي", "Honor of king": "أونر أوف كينغز",
    "King Shot": "كينغ شوت", "Lords mobile": "لوردوز موبايل", "Ludo clud": "لودو كلود",
    "Marvel Reveals": "مارفل ريفيلز", "Oxide": "أوكسيد", "Project entropy": "بروجكت إنتروبيا",
    "Stumble Guys": "ستامبل جايز", "Super SUS": "سوبر ساس", "Whiteout Survival": "وايت آوت",
    "Yalla Ludo": "يلا لودو", "Zepeto": "زيبتو", "afk journey": "AFK Journey",
    "age of magic": "عصر السحر", "arena of valor": "أرينا أوف فالور", "arknights": "أرك نايتس",
    "asphalt": "أسبالت 9", "astral guardians": "أسترو غارديون", "badlanders": "بادلاندرز",
    "ballistic hero": "باليستيك هيرو", "be the king": "كن ملكاً", "blade x": "بليد إكس",
    "blockman go": "بلوكمان جو", "captain tsubasa": "كابتن تسوباسا", "civilization eras": "حضارات",
    "clash of plants": "صراع النباتات", "cloud song": "أغنية السحاب", "crossfire legend": "كروس فاير",
    "crossout mobile": "كروس أوت", "crystalfall": "كريستالفول", "deadly dudes": "ديدلي دادز",
    "division resurgence": "ديفيجن", "dragon nest": "عش التنين", "dragon raja": "دراغون راجا",
    "dragonheir": "دراغون وريث", "dream and lethe": "حلم وليث", "duet night": "دويت نايت",
    "dynasty heroes": "أبطال السلالة", "echocalypse": "إيكوكاليبس", "eggy party": "إيجي بارتي",
    "enhypen world": "عالم إنهايين", "ensemble stars": "أونسامبل ستارز", "eve echoes": "إيف إيكوز",
    "extraordinary ones": "الأستثنائيون", "football master": "ماستر كرة القدم", "garena speed": "غار ينا سبيد",
    "gearup booster": "غير أب بوستر", "ghost story": "قصة الأشباح", "goddess of victory": "إلهة النصر",
    "golden spatula": "السباتيولا الذهبية", "growtopia": "جروتوبيا", "haikyu fly": "هايكيو",
    "hatsune miku": "هاتسون ميكو", "heartopia": "هيرتوبيا", "heaven burns": "هيفن بيرنز",
    "hyper front": "هايبر فرونت", "identity v": "آي دي إيكي", "idol party": "آيدول بارتي",
    "infinite lagrange": "لانغراج بلا حدود", "isekai feast": "وليمة إيساكي", "jade dynasty": "سلالة اليشم",
    "journey renewed": "رحلة متجددة", "kings choice": "اختيار الملك", "kuroko street": "شارع كوركو",
    "legacy of discord": "إرث الخلاف", "legend of the phoenix": "أسطورة العنقاء", "life makeover": "حياة جديدة",
    "love and deepspace": "حب وعمق", "love nikki": "حب نيكي", "magic chess": "شطرنج السحر",
    "marvel duel": "مارفل دويل", "marvel mystic": "مارفل ميستيك", "mirage perfect": "ميراج",
    "modern strike": "ضربة حديثة", "mongil star": "مونجال ستار", "my singing monsters": "وحوشي الغنائية",
    "onmyoji arena": "أونميوجي", "overmortal idle": "أوفرمورتال", "stormshot": "ستورم شوت",
    "Punishing": "بانيشينغ", "Revenge of Sultans": "انتقام السلاطين", "Brawl": "براول ستارز",
    "eFootball": "إي فوتبول", "FarLight": "فارلايت", "Blizard": "بليزارد",
}


def classify(cat_name: str) -> str:
    """يرجع نوع القسم المناسب لفئة المزود."""
    cl = cat_name.lower() if cat_name else ""
    for keyword in CATEGORY_MAP["balances"]:
        if keyword.lower() in cl:
            return "balances"
    for keyword in CATEGORY_MAP["cards"]:
        if keyword.lower() in cl:
            return "cards"
    for keyword in CATEGORY_MAP["apps"]:
        if keyword.lower() in cl:
            return "apps"
    for keyword in CATEGORY_MAP["games"]:
        if keyword.lower() in cl:
            return "games"
    return "apps"  # افتراضي


def translate_game(name: str) -> str:
    for en, ar in AR_NAMES.items():
        if en.lower() in name.lower():
            return ar
    return name


async def main():
    await init_db()
    async with async_session_maker() as session:
        provider = (await session.execute(
            select(ApiProvider).where(ApiProvider.name == "HyperStore")
        )).scalar_one_or_none()
        if not provider:
            print("❌ لا يوجد مزود HyperStore")
            return

        # جلب كل خدمات المزود ومنتجاتها
        services = (await session.execute(
            select(ProviderService).where(ProviderService.api_provider_id == provider.id)
        )).scalars().all()

        moved = {g: 0 for g in ["games", "apps", "balances", "cards"]}
        for svc in services:
            cat_type = classify(svc.category or "")
            moved[cat_type] = moved.get(cat_type, 0) + 1

            # تحديد القسم الصحيح في البوت
            ct = {"games": CategoryType.GAMES, "apps": CategoryType.APPS,
                  "balances": CategoryType.BALANCES, "cards": CategoryType.CARDS}[cat_type]

            # إيجاد أو إنشاء القسم الرئيسي
            result = await session.execute(select(Category).where(Category.type == ct))
            category = result.scalar_one_or_none()
            if not category:
                names = {CategoryType.GAMES: ("شحن الألعاب", "🎮"), CategoryType.APPS: ("تطبيقات ودعم ولايفات", "📦"),
                         CategoryType.BALANCES: ("رصيد محلي", "📱"), CategoryType.CARDS: ("البطاقات والفيز", "💳")}
                n, e = names[ct]
                category = Category(name_ar=n, emoji=e, type=ct, is_active=True, sort_order=100)
                session.add(category)
                await session.flush()

            # القسم الفرعي = فئة المزود (مع ترجمة الألعاب)
            sub_name = translate_game(svc.category or svc.name)[:64]
            result = await session.execute(select(SubCategory).where(
                SubCategory.category_id == category.id, SubCategory.name_ar == sub_name))
            sub = result.scalar_one_or_none()
            if not sub:
                sub = SubCategory(category_id=category.id, name_ar=sub_name, emoji="📦")
                session.add(sub)
                await session.flush()

            # نقل المنتجات
            products = (await session.execute(
                select(Product).where(Product.provider_service_ref_id == svc.id)
            )).scalars().all()
            for p in products:
                p.sub_category_id = sub.id
                # تحديث الاسم العربي إذا كان لعبة
                if cat_type == "games":
                    p.name_ar = translate_game(p.name_ar)[:128]

        await session.commit()
        print(f"✅ إعادة التصنيف اكتملت:")
        for g, c in moved.items():
            print(f"  • {g}: {c} خدمة")

        # إحصاء المنتجات
        for ct in [CategoryType.GAMES, CategoryType.APPS, CategoryType.BALANCES, CategoryType.CARDS]:
            cnt = (await session.execute(
                select(Product).join(SubCategory, Product.sub_category_id == SubCategory.id)
                .join(Category, SubCategory.category_id == Category.id)
                .where(Category.type == ct)
            )).scalars().all()
            print(f"  • {ct.value}: {len(cnt)} منتج")


if __name__ == "__main__":
    asyncio.run(main())
