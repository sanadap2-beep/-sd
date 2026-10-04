"""Tenant deposits: end-user funds, merchant approves in main bot."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import DepositRequest, DepositStatus, Tenant, User
from services.html_guard import esc
from states.states import TenantDepositStates

router = Router(name="tenant_deposit")


@router.callback_query(F.data == "t:deposit")
async def tenant_deposit_start(callback: CallbackQuery, state: FSMContext, tenant: Tenant):
    await state.set_state(TenantDepositStates.waiting_amount)
    await callback.message.answer(
        f"➕ <b>شحن الرصيد في {esc(tenant.brand_name)}</b>\n\n"
        "أرسل المبلغ بالدولار (مثال: 5):"
    )
    await callback.answer()


@router.message(TenantDepositStates.waiting_amount)
async def tenant_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ أدخل رقماً صالحاً.")
        return
    if amount <= 0 or amount > 10000:
        await message.answer("⚠️ المبلغ يجب أن يكون بين 0 و 10000$.")
        return
    await state.update_data(dep_amount=str(amount))
    await state.set_state(TenantDepositStates.waiting_proof)
    await message.answer(
        "🧾 أرسل رقم عملية التحويل أو أي إثبات دفع (حسب طريقة الدفع التي يعلنها المتجر):"
    )


@router.message(TenantDepositStates.waiting_proof)
async def tenant_deposit_proof(
    message: Message, state: FSMContext, session, db_user: User, tenant: Tenant
):
    data = await state.get_data()
    await state.clear()
    proof = (message.text or "").strip()[:255]
    if len(proof) < 3:
        await message.answer("⚠️ إثبات غير صالح.")
        return
    req = DepositRequest(
        tenant_id=tenant.id,
        user_id=db_user.id,
        amount_usd=Decimal(data["dep_amount"]),
        proof_tx_number=proof,
        payment_method="tenant_manual",
        status=DepositStatus.PENDING,
    )
    session.add(req)
    await session.commit()
    await message.answer(
        "✅ استلمنا طلب الشحن — سيُراجَع ويُضاف لرصيدك قريباً.\n"
        f"المبلغ: <b>{data['dep_amount']}$</b>"
    )
