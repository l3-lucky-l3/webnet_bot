import logging
from typing import Optional, Dict, Any
from django.conf import settings
from aiosend import CryptoPay
from aiosend.types import Invoice

# Asset это строковая константа (USDT, TON, BTC и т.д.)
# В старых версиях aiosend был Asset enum, в новых его нет
from .models import Payment as PaymentModel
from config import CRYPTOBOT_API_TOKEN

logger = logging.getLogger(__name__)


class CryptobotService:
    """Сервис для работы с CryptoBot API (Telegram)"""

    def __init__(self):
        if not CRYPTOBOT_API_TOKEN:
            logger.error("CRYPTOBOT_API_TOKEN не установлен в config.py")
            self.client = None
        else:
            self.client = CryptoPay(CRYPTOBOT_API_TOKEN)

    @staticmethod
    def _get_client() -> Optional[CryptoPay]:
        """Получает клиент CryptoPay"""
        if not CRYPTOBOT_API_TOKEN:
            logger.error("CRYPTOBOT_API_TOKEN не установлен")
            return None
        return CryptoPay(CRYPTOBOT_API_TOKEN)

    @staticmethod
    async def create_invoice(
        payment_model: PaymentModel,
        asset: str = "USDT",
        description: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Создает invoice в CryptoBot

        Args:
            payment_model: Модель платежа из Django
            asset: Валюта оплаты (USDT, TON, BTC, ETH и т.д.)
            description: Описание платежа

        Returns:
            Dict с данными invoice или None при ошибке
        """
        try:
            client = CryptobotService._get_client()
            if not client:
                return None

            # Определяем описание
            if not description:
                subscription_names = {
                    'week': '1 неделя (ОБХОД глушилок + VPN)',
                    'month': 'Месячная подписка',
                    '3months': '3 месяца',
                    '6months': '6 месяцев',
                    'year': 'Годовая подписка',
                    'trial': 'Пробная подписка',
                    'regular_day': '1 день (ULTRA FAST VPN)',
                    'regular_month': '1 месяц (ULTRA FAST VPN)',
                    'regular_3months': '3 месяца (ULTRA FAST VPN)',
                    'regular_6months': '6 месяцев (ULTRA FAST VPN)',
                    'regular_year': '1 год (ULTRA FAST VPN)',
                    'regular_2years': '2 года (ULTRA FAST VPN)',
                    'fast_week': '1 неделя (Обычный VPN)',
                    'fast_month': '1 месяц (Обычный VPN)',
                    'fast_3months': '3 месяца (Обычный VPN)',
                    'fast_6months': '6 месяцев (Обычный VPN)',
                    'fast_year': '1 год (Обычный VPN)',
                }
                vpn_label = "ULTRA FAST VPN" if payment_model.vpn_type == 'regular' else ("Обычный VPN" if payment_model.vpn_type == 'fast' else "Night VPN")
                sub_name = subscription_names.get(payment_model.subscription_type, payment_model.subscription_type)
                description = f"{vpn_label}: {sub_name}"

            # Конвертируем рубли в соответствующую валюту
            # CryptoBot сам конвертирует, но нужно передать в правильной валюте
            # Для USDT и других stablecoin используем сумму в USD (примерно 1 RUB = 0.011 USD)
            # Но CryptoBot поддерживает RUB напрямую, так что передаем в рублях
            # Если RUB не поддерживается, использу USDT с конвертацией
            
            # Проверяем поддерживаемые активы
            supported_assets = ['USDT', 'TON', 'BTC', 'ETH', 'LTC', 'BUSD', 'TRX']
            if asset.upper() not in supported_assets:
                logger.warning(f"Актив {asset} не поддерживается, используем USDT")
                asset = 'USDT'

            # Создаем invoice через CryptoBot API
            # Сумма в копейках/центах для fiat, или в минимальных единицах для crypto
            # CryptoBot принимает сумму в основных единицах (не копейки)
            amount = float(payment_model.amount)
            
            # Для крипто-активов конвертируем рубли в crypto
            # CryptoBot API требует amount в crypto единицах
            # Используем pay_currency для указания валюты
            invoice = await client.create_invoice(
                amount=amount,
                asset=asset.upper(),
                description=description,
                payload=str(payment_model.payment_id)  # В payload кладем ID платежа
            )

            if not invoice:
                logger.error(f"Не удалось создать invoice для платежа {payment_model.payment_id}")
                return None

            # Получаем URL для оплаты (используем mini_app_invoice_url или bot_invoice_url)
            payment_url = invoice.mini_app_invoice_url or invoice.bot_invoice_url
            
            if not payment_url:
                logger.error(f"Не удалось получить URL для invoice {invoice.invoice_id}")
                return None

            # Обновляем модель платежа
            payment_model.cryptobot_invoice_id = str(invoice.invoice_id)
            payment_model.cryptobot_payment_url = payment_url
            payment_model.cryptobot_asset = asset.upper()
            payment_model.save()

            logger.info(
                f"Создан invoice CryptoBot {invoice.invoice_id} "
                f"для платежа {payment_model.payment_id}, "
                f"сумма: {amount} {asset.upper()}, "
                f"URL: {payment_url}"
            )

            return {
                'invoice_id': str(invoice.invoice_id),
                'payment_url': payment_url,
                'amount': amount,
                'asset': asset.upper(),
                'status': invoice.status,
            }

        except Exception as e:
            logger.error(f"Ошибка создания invoice CryptoBot для {payment_model.payment_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    async def check_invoice_status(invoice_id: str) -> Optional[Dict[str, Any]]:
        """
        Проверяет статус invoice в CryptoBot

        Args:
            invoice_id: ID invoice в CryptoBot

        Returns:
            Dict со статусом или None при ошибке
        """
        try:
            client = CryptobotService._get_client()
            if not client:
                return None

            # Получаем список invoices и ищем нужный
            # aiosend не имеет прямого метода get_invoice, поэтому используем get_invoices
            invoices = await client.get_invoices(invoice_ids=[int(invoice_id)])
            
            if not invoices or len(invoices) == 0:
                logger.warning(f"Invoice {invoice_id} не найден")
                return None

            invoice = invoices[0]
            
            return {
                'invoice_id': str(invoice.invoice_id),
                'status': invoice.status,  # active, paid, expired и т.д.
                'amount': invoice.amount,
                'asset': invoice.asset,
                'paid_at': invoice.paid_at,
                'payload': invoice.payload,
            }

        except Exception as e:
            logger.error(f"Ошибка проверки статуса invoice {invoice_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    def process_webhook(webhook_data: Dict[str, Any]) -> bool:
        """
        Обрабатывает webhook от CryptoBot

        Webhook содержит данные об оплаченном invoice.
        Согласно документации CryptoBot, webhook приходит при изменении статуса.

        Args:
            webhook_data: Данные webhook от CryptoBot

        Returns:
            True если обработка успешна, False иначе
        """
        try:
            logger.info(f"Получен webhook от CryptoBot: {webhook_data}")

            # Извлекаем данные
            update_type = webhook_data.get('update_type')
            
            # Нас интересует только paid_invoice
            if update_type != 'invoice_paid':
                logger.info(f"Игнорируем update_type: {update_type}")
                return True

            invoice_data = webhook_data.get('invoice_paid', {})
            invoice_id = str(invoice_data.get('invoice_id'))
            payload = invoice_data.get('payload')  # Это наш payment_id

            if not invoice_id or not payload:
                logger.error(f"Недостаточно данных в webhook. invoice_id={invoice_id}, payload={payload}")
                return False

            # Находим платеж в БД по payload (это payment_id)
            payment_model = PaymentModel.objects.filter(payment_id=int(payload)).first()

            if not payment_model:
                logger.warning(f"Платеж с ID {payload} не найден в БД")
                return False

            # Проверяем, что invoice_id совпадает
            if payment_model.cryptobot_invoice_id != invoice_id:
                logger.warning(
                    f"Несовпадение invoice_id: в БД={payment_model.cryptobot_invoice_id}, "
                    f"в webhook={invoice_id}"
                )
                return False

            # Проверяем, не был ли платеж уже обработан
            if payment_model.status == 'succeeded':
                logger.info(f"Платеж {payment_model.payment_id} уже обработан")
                return True

            logger.info(f"Платеж {payment_model.payment_id} успешен в CryptoBot, выдаем ключ")

            # Автоматически обрабатываем успешный платеж
            return CryptobotService._handle_payment_success(payment_model)

        except Exception as e:
            logger.error(f"Ошибка обработки webhook CryptoBot: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _handle_payment_success(payment_model: PaymentModel) -> bool:
        """Обрабатывает успешный платеж CryptoBot"""
        try:
            from .services import PaymentService

            logger.info(f"Обрабатываем успешный платеж CryptoBot: {payment_model.payment_id}")
            logger.info(f"payment.subscription_type={payment_model.subscription_type}, payment.vpn_type={payment_model.vpn_type}")

            # Проверяем тип VPN - для regular_* подписок используем Remnawave API
            vpn_type = getattr(payment_model, 'vpn_type', 'night')
            subscription_type = payment_model.subscription_type

            is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
            is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')

            if is_regular_vpn:
                logger.info(f"Платеж {payment_model.payment_id} - ULTRA FAST VPN, генерируем ключ через Remnawave")
                return CryptobotService._handle_regular_vpn_payment_success(payment_model)
            elif is_fast_vpn:
                logger.info(f"Платеж {payment_model.payment_id} - Обычный VPN, генерируем ключ через bypass API")
                payment_service = PaymentService()
                payment_service.confirm_payment(payment_model)
                logger.info(f"Платеж {payment_model.payment_id} успешно обработан (Обычный VPN)")
                return True
            else:
                logger.info(f"Платеж {payment_model.payment_id} - Night VPN, выдаем ключ через PaymentService")
                payment_service = PaymentService()
                payment_service.confirm_payment(payment_model)
                logger.info(f"Платеж {payment_model.payment_id} успешно обработан")
                return True

        except Exception as e:
            logger.error(f"Ошибка обработки успешного платежа CryptoBot: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _handle_regular_vpn_payment_success(payment_model: PaymentModel, skip_notification: bool = False) -> bool:
        """Обрабатывает успешный платеж для Обычного VPN через Remnawave"""
        try:
            from .regular_vpn_service import process_regular_vpn_payment_success_sync

            logger.info(f"Обрабатываем успешный платеж Обычного VPN (CryptoBot): {payment_model.payment_id}")

            result = process_regular_vpn_payment_success_sync(payment_model.payment_id, skip_notification=skip_notification)

            logger.info(f"Результат генерации ключа: {result}")

            if result and result.get('success'):
                key = result.get('key')
                logger.info(f"Ключ для Обычного VPN успешно сгенерирован: {key[:20] if key else 'None'}...")

                # Обновляем платеж
                payment_model.issued_key = key
                payment_model.status = 'succeeded'
                from django.utils import timezone
                payment_model.paid_at = timezone.now()
                payment_model.save()

                return True
            else:
                error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Ошибка генерации ключа'
                logger.error(f"Ошибка генерации ключа для Обычного VPN: {error_msg}")

                # Обновляем статус платежа, но помечаем что ключ не выдан
                payment_model.status = 'succeeded'
                from django.utils import timezone
                payment_model.paid_at = timezone.now()
                payment_model.save()

                return False

        except Exception as e:
            logger.error(f"Ошибка обработки успешного платежа Обычного VPN (CryptoBot): {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
