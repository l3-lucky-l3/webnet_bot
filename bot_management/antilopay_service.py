import json
import logging
import base64
import requests
from typing import Optional, Dict, Any
from django.utils import timezone
from .models import Payment as PaymentModel
from config import ANTILOPAY_SECRET_ID, ANTILOPAY_PRIVATE_KEY, ANTILOPAY_PROJECT_ID

logger = logging.getLogger(__name__)

ANTILOPAY_BASE_URL = 'https://lk.antilopay.com/api/v1'


class AntilopayService:
    """Сервис для работы с Antilopay API (только СБП)"""

    @staticmethod
    def _get_recurrent_type(subscription_type: str) -> str:
        """Определяет тип интервала рекуррентного платежа.
        API Antilopay принимает только WEEK или MONTH.
        """
        week_subscriptions = {'week', 'trial', 'regular_day', 'fast_week'}
        base = subscription_type.replace('regular_', '').replace('fast_', '')
        return 'WEEK' if base in week_subscriptions else 'MONTH'

    @staticmethod
    def _get_delay_type(subscription_type: str) -> str:
        """delay_type всегда DAY, так как delay указан в днях"""
        return 'DAY'

    @staticmethod
    def _sign(data: str) -> str:
        """
        Формирует SHA256WithRSA подпись запроса.

        Args:
            data: JSON строка тела запроса

        Returns:
            Base64 подпись
        """
        try:
            from Crypto.Hash import SHA256
            from Crypto.PublicKey import RSA
            from Crypto.Signature import pkcs1_15

            private_key = ANTILOPAY_PRIVATE_KEY
            if not private_key:
                logger.error("ANTILOPAY_PRIVATE_KEY не установлен")
                return ""

            rsa_key = RSA.importKey(base64.b64decode(private_key))
            payload = bytes(data, 'UTF-8')
            hash_obj = SHA256.new(payload)
            sign = base64.b64encode(pkcs1_15.new(rsa_key).sign(hash_obj))
            return sign.decode('utf-8')

        except Exception as e:
            logger.error(f"Ошибка подписи Antilopay: {e}")
            return ""

    @staticmethod
    def _headers(body: str) -> Dict[str, str]:
        """Формирует заголовки для запроса к Antilopay API"""
        sign = AntilopayService._sign(body)
        return {
            'Content-Type': 'application/json',
            'X-Apay-Secret-Id': ANTILOPAY_SECRET_ID,
            'X-Apay-Sign': sign,
            'X-Apay-Sign-Version': '1',
        }

    @staticmethod
    def create_payment(payment_model: PaymentModel, success_url: str = None, fail_url: str = None, delay: int = 0) -> Optional[Dict[str, Any]]:
        """
        Создает платеж в Antilopay через СБП.

        Args:
            payment_model: Модель платежа из Django
            success_url: URL для возврата после успешной оплаты
            fail_url: URL для возврата после неудачной оплаты
            delay: Задержка перед первым рекуррентным списанием (в днях). 0 = сразу после привязки.

        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            if not ANTILOPAY_SECRET_ID or not ANTILOPAY_PRIVATE_KEY or not ANTILOPAY_PROJECT_ID:
                logger.error("Antilopay credentials не установлены (SECRET_ID, PRIVATE_KEY, PROJECT_ID)")
                return None

            if not success_url:
                bot_username = "webnetvpn_robot"
                success_url = f"https://t.me/{bot_username}?start=payment_success_{payment_model.payment_id}"

            if not fail_url:
                bot_username = "webnetvpn_robot"
                fail_url = f"https://t.me/{bot_username}?start=payment_failed_{payment_model.payment_id}"

            subscription_names = {
                'week': '1 неделя (ОБХОД глушилок + VPN)',
                'month': 'Месячная подписка',
                '3months': '3 месяца',
                '6months': '6 месяцев',
                'year': 'Годовая подписка',
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
            description = f"Оплата подписки: {subscription_names.get(payment_model.subscription_type, payment_model.subscription_type)}"

            # Проверяем, есть ли уже созданный платеж в Antilopay для этого payment_id
            if payment_model.antilopay_payment_id and payment_model.antilopay_payment_url:
                logger.info(f"Платеж Antilopay уже создан: payment_id={payment_model.payment_id}, antilopay_id={payment_model.antilopay_payment_id}")
                # Проверяем статус существующего платежа
                check_result = AntilopayService.check_payment(ANTILOPAY_PROJECT_ID, str(payment_model.payment_id))
                if check_result:
                    status = check_result.get('status')
                    logger.info(f"Статус существующего платежа: {status}")
                    if status in ('PENDING', 'WAIT_CONFIRM', 'PROCESSING'):
                        logger.info(f"Возвращаем существующий URL для платежа {payment_model.payment_id}")
                        return {
                            'payment_id': payment_model.antilopay_payment_id,
                            'payment_url': payment_model.antilopay_payment_url,
                            'status': status,
                            'amount': payment_model.amount,
                            'currency': 'RUB',
                            'description': description,
                        }
                logger.info(f"Существующий платеж в терминальном статусе, пробуем создать новый с уникальным order_id")

            # Для избежания дублирования order_id используем уникальный идентификатор
            unique_order_id = f"{payment_model.payment_id}_{timezone.now().strftime('%Y%m%d%H%M%S')}"
            
            body_dict = {
                'project_identificator': ANTILOPAY_PROJECT_ID,
                'amount': float(payment_model.amount),
                'order_id': unique_order_id,
                'currency': 'RUB',
                'product_name': description[:100],
                'product_type': 'services',
                'description': description,
                'success_url': success_url,
                'fail_url': fail_url,
                'customer': {
                    'email': 'payment@antilopay.com',
                },
                'prefer_methods': ['SBP'],
                'recurrent': {
                    'type': AntilopayService._get_recurrent_type(payment_model.subscription_type),
                    'payment_count': 60,
                    'category': 'SUBSCRIPTION',
                    'delay': delay,
                    'delay_type': AntilopayService._get_delay_type(payment_model.subscription_type),
                },
            }

            body = json.dumps(body_dict, separators=(',', ':'))
            headers = AntilopayService._headers(body)

            logger.info(f"Создание платежа Antilopay: payment_id={payment_model.payment_id}, amount={payment_model.amount}, order_id={unique_order_id}")

            response = requests.post(
                f'{ANTILOPAY_BASE_URL}/payment/create',
                data=body,
                headers=headers,
                timeout=30
            )

            logger.info(f"Antilopay API response status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Antilopay API response: {result}")

                code = result.get('code')
                if code != 0:
                    error_msg = result.get('error', f'Antilopay error code: {code}')
                    logger.error(f"Antilopay API error: {error_msg}")
                    logger.error(f"Antilopay full response: {result}")
                    logger.error(f"Antilopay request body: {body}")
                    logger.error(f"Antilopay request headers: X-Apay-Secret-Id={ANTILOPAY_SECRET_ID[:8]}...")
                    return None

                payment_id_ap = result.get('payment_id')
                payment_url = result.get('payment_url')
                recurrent_id = result.get('recurrent_id')

                if not payment_id_ap or not payment_url:
                    logger.error(f"Неполный ответ от Antilopay: payment_id={payment_id_ap}, payment_url={payment_url}")
                    return None

                payment_model.antilopay_payment_id = payment_id_ap
                payment_model.antilopay_payment_url = payment_url
                if recurrent_id:
                    payment_model.antilopay_recurrent_id = recurrent_id
                payment_model.save()

                logger.info(f"Создан платеж Antilopay {payment_id_ap} для платежа {payment_model.payment_id}")

                return {
                    'payment_id': payment_id_ap,
                    'payment_url': payment_url,
                    'status': 'PENDING',
                    'amount': payment_model.amount,
                    'currency': 'RUB',
                    'description': description,
                }
            else:
                error_text = response.text
                logger.error(f"Antilopay API error: {response.status_code} - {error_text}")
                return None

        except Exception as e:
            logger.error(f"Ошибка создания платежа Antilopay для {payment_model.payment_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    def check_payment(project_id: str, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Проверяет статус платежа в Antilopay.

        Args:
            project_id: Идентификатор проекта
            order_id: Идентификатор платежа со стороны мерчанта

        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            if not ANTILOPAY_SECRET_ID or not ANTILOPAY_PRIVATE_KEY:
                logger.error("Antilopay credentials не установлены")
                return None

            body_dict = {
                'project_identificator': project_id,
                'order_id': order_id,
            }

            body = json.dumps(body_dict, separators=(',', ':'))
            headers = AntilopayService._headers(body)

            response = requests.post(
                f'{ANTILOPAY_BASE_URL}/payment/check',
                data=body,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                code = result.get('code')
                if code != 0:
                    error_msg = result.get('error', f'code: {code}')
                    logger.info(f"Antilopay check error: {error_msg}")
                    # Если платеж еще не найден в Antilopay (создан недавно), считаем его PENDING
                    if code in (3, 5) or 'not found' in error_msg.lower() or 'Duplicated' in error_msg:
                        logger.info(f"Платеж {order_id} еще не найден в Antilopay, возвращаем PENDING")
                        return {
                            'status': 'PENDING',
                            'order_id': order_id,
                            'amount': 0,
                            'currency': 'RUB',
                        }
                    return None
                return result
            else:
                logger.error(f"Antilopay check HTTP error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Ошибка проверки статуса Antilopay: {e}")
            return None

    @staticmethod
    def check_recurrent_payment_status(recurrent_id: str) -> Optional[Dict[str, Any]]:
        """
        Проверяет статус рекуррентного платежа (метод 5.3 из документации Antilopay).

        Args:
            recurrent_id: UUID рекуррентного платежа

        Returns:
            Dict с данными статуса или None при ошибке
        """
        try:
            if not ANTILOPAY_SECRET_ID or not ANTILOPAY_PRIVATE_KEY:
                logger.error("Antilopay credentials не установлены")
                return None

            body_dict = {
                'project_identificator': ANTILOPAY_PROJECT_ID,
                'recurrent_id': recurrent_id,
            }

            body = json.dumps(body_dict, separators=(',', ':'))
            headers = AntilopayService._headers(body)

            logger.info(f"Проверка статуса рекуррентного платежа: recurrent_id={recurrent_id}")

            response = requests.post(
                f'{ANTILOPAY_BASE_URL}/payment/recurrent/check',
                data=body,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                code = result.get('code')
                if code != 0:
                    logger.error(f"Antilopay recurrent status error: {result.get('error', f'code: {code}')}")
                    return None
                logger.info(f"Статус рекуррентного платежа: {result}")
                return result
            else:
                logger.error(f"Antilopay recurrent status HTTP error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Ошибка проверки статуса рекуррентного платежа: {e}")
            return None

    @staticmethod
    def verify_callback_signature(body: str, signature: str) -> bool:
        """
        Проверяет подпись callback от Antilopay.

        Args:
            body: JSON строка тела callback
            signature: Подпись из заголовка X-Apay-Callback (Base64)

        Returns:
            True если подпись верна
        """
        try:
            from Crypto.Hash import SHA256
            from Crypto.PublicKey import RSA
            from Crypto.Signature import pkcs1_15
            from config import ANTILOPAY_CALLBACK_PUBLIC_KEY

            public_key_b64 = ANTILOPAY_CALLBACK_PUBLIC_KEY
            if not public_key_b64:
                logger.warning("ANTILOPAY_CALLBACK_PUBLIC_KEY не установлен, пропускаем проверку подписи")
                return True

            rsa_key = RSA.importKey(base64.b64decode(public_key_b64))
            sign_raw = base64.b64decode(signature)
            payload = bytes(body, 'UTF-8')
            hash_obj = SHA256.new(payload)
            pkcs1_15.new(rsa_key).verify(hash_obj, sign_raw)
            logger.info("Подпись callback Antilopay верна")
            return True

        except (ValueError, TypeError) as e:
            logger.error(f"Подпись callback Antilopay не верна: {e}")
            return False
        except Exception as e:
            logger.error(f"Ошибка проверки подписи callback Antilopay: {e}")
            return False

    @staticmethod
    def process_webhook(callback_data: Dict[str, Any], skip_notification: bool = False) -> bool:
        """
        Обрабатывает callback от Antilopay.

        Args:
            callback_data: Данные callback от Antilopay
            skip_notification: Пропустить отправку уведомления

        Returns:
            True если обработка успешна
        """
        try:
            logger.info(f"Получен callback от Antilopay: {callback_data}")

            type_field = callback_data.get('type')
            if type_field != 'payment':
                logger.info(f"Antilopay callback type не payment: {type_field}, пропускаем")
                return False

            payment_id_ap = callback_data.get('payment_id')
            order_id = callback_data.get('order_id')
            status = callback_data.get('status')
            recurrent_id = callback_data.get('recurrent_id')
            amount = callback_data.get('amount')

            if not payment_id_ap or not status:
                logger.error(f"Недостаточно данных в callback: payment_id={payment_id_ap}, status={status}")
                return False

            # Ищем платеж по antilopay_payment_id или order_id
            payment_model = PaymentModel.objects.filter(
                antilopay_payment_id=payment_id_ap
            ).first()

            if not payment_model:
                # order_id может быть в формате "{payment_id}_{timestamp}" или "{payment_id}_{timestamp}_R1"
                # Извлекаем базовый payment_id из order_id
                base_order_id = order_id
                if '_R' in order_id:
                    # Рекуррентный платёж: "69017_20260802221111_R1" -> "69017"
                    base_order_id = order_id.split('_R')[0]
                if '_' in base_order_id:
                    # Уникальный order_id с timestamp: "69017_20260802221111" -> "69017"
                    parts = base_order_id.split('_')
                    if len(parts) >= 2 and parts[-1].isdigit():
                        base_order_id = parts[0]
                
                payment_model = PaymentModel.objects.filter(
                    payment_id=base_order_id
                ).first()
                
                if not payment_model and base_order_id != order_id:
                    # Пробуем также найти по полному order_id (на случай если payment_id содержит подчёркивания)
                    payment_model = PaymentModel.objects.filter(
                        payment_id=order_id
                    ).first()

            # Если это рекуррентный платёж (новый payment_id, но есть recurrent_id)
            if not payment_model and recurrent_id:
                logger.info(f"Рекуррентный callback, ищем исходный платеж по recurrent_id={recurrent_id}")
                original_payment = PaymentModel.objects.filter(
                    antilopay_recurrent_id=recurrent_id
                ).order_by('-created_at').first()

                if original_payment and status == 'SUCCESS':
                    logger.info(f"Найден исходный платеж #{original_payment.payment_id} для рекуррента")

                    # По требованию Antilopay: проверяем статус рекуррента перед выдачей товара (п.5.2)
                    try:
                        recurrent_status_data = AntilopayService.check_recurrent_payment_status(recurrent_id)
                        if recurrent_status_data:
                            r_status = recurrent_status_data.get('status', '')
                            logger.info(f"Статус рекуррента перед выдачей товара: {r_status}")
                            if r_status in ('ACTIVE', 'WAIT_CONFIRM', 'PROCESSING', 'CREATED'):
                                return AntilopayService._handle_recurrent_payment_success(
                                    original_payment, payment_id_ap, callback_data, skip_notification
                                )
                            elif r_status in ('CANCEL', 'PROVIDER_CANCEL', 'ERROR'):
                                logger.warning(f"Рекуррент {recurrent_id} в статусе {r_status} — товар не выдаём")
                                return False
                            else:
                                logger.warning(f"Неизвестный статус рекуррента: {r_status}")
                                return False
                        else:
                            logger.error(f"Не удалось получить статус рекуррента {recurrent_id}")
                            return False
                    except Exception as e:
                        logger.error(f"Ошибка проверки рекуррента при charge callback: {e}")
                        return False

            if not payment_model:
                logger.warning(f"Платеж с antilopay_payment_id={payment_id_ap} или order_id={order_id} не найден")
                return False

            logger.info(f"Найден платеж: payment_id={payment_model.payment_id}, user_id={payment_model.user.user_id}")

            if status == 'SUCCESS':
                if payment_model.status == 'succeeded':
                    logger.info(f"Платеж {payment_model.payment_id} уже обработан")
                    return True

                is_binding = (amount is not None and float(amount) == 0)

                if is_binding:
                    logger.info(f"Платеж {payment_model.payment_id} — привязка SUBSCRIPTION (amount=0)")
                    if recurrent_id:
                        payment_model.antilopay_recurrent_id = recurrent_id
                        payment_model.save()
                        logger.info(f"Сохранён recurrent_id={recurrent_id} для платежа {payment_model.payment_id}")

                    # Проверяем статус рекуррента сразу
                    try:
                        recurrent_status_data = AntilopayService.check_recurrent_payment_status(recurrent_id)
                        if recurrent_status_data:
                            r_status = recurrent_status_data.get('status', '')
                            logger.info(f"Статус рекуррента после binding: {r_status}")
                            if r_status in ('CANCEL', 'PROVIDER_CANCEL', 'ERROR'):
                                logger.warning(f"Рекуррент {recurrent_id} в статусе {r_status} — связка не удалась. Платеж обработан как разовый.")
                                # Antilopay мог обработать SBP как разовый платёж — пробуем проверить
                                check_data = AntilopayService.check_payment(ANTILOPAY_PROJECT_ID, str(payment_model.payment_id))
                                if check_data:
                                    ap_amount = check_data.get('amount', 0)
                                    ap_status = check_data.get('status', '')
                                    if ap_status == 'SUCCESS' and float(ap_amount) > 0:
                                        logger.info(f"Платёж {payment_model.payment_id} успешен как разовой (amount={ap_amount}), выдаём ключ")
                                        result = AntilopayService._handle_payment_success(payment_model, skip_notification=skip_notification)
                                        if result:
                                            payment_model.refresh_from_db()
                                            AntilopayService._notify_user(payment_model)
                                        return result
                    except Exception as e:
                        logger.error(f"Ошибка проверки статуса рекуррента при binding: {e}")

                    return True

                logger.info(f"Платеж {payment_model.payment_id} успешен, выдаем ключ")
                result = AntilopayService._handle_payment_success(payment_model, skip_notification=skip_notification)
                if result:
                    payment_model.refresh_from_db()
                    AntilopayService._notify_user(payment_model)
                return result

            elif status in ('FAIL', 'CANCEL', 'EXPIRED'):
                if payment_model.status != 'canceled':
                    payment_model.status = 'canceled'
                    payment_model.save()
                    logger.info(f"Статус платежа {payment_model.payment_id} обновлен на 'canceled' (Antilopay: {status})")
                return True
            else:
                logger.warning(f"Неизвестный статус Antilopay: {status}")
                return False

        except Exception as e:
            logger.error(f"Ошибка обработки callback Antilopay: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _handle_recurrent_payment_success(
        original_payment: PaymentModel,
        new_payment_id_ap: str,
        callback_data: Dict[str, Any],
        skip_notification: bool = False
    ) -> bool:
        """
        Обрабатывает успешный рекуррентный платёж — продлевает подписку и сбрасывает трафик.

        Args:
            original_payment: Исходный платеж (первый платёж по подписке)
            new_payment_id_ap: Новый payment_id от Antilopay
            callback_data: Данные callback
            skip_notification: Пропустить отправку уведомления

        Returns:
            True если продление успешно
        """
        try:
            from django.utils import timezone
            from datetime import timedelta

            logger.info(f"Обрабатываем рекуррентный платёж: original_payment=#{original_payment.payment_id}, new_payment_id={new_payment_id_ap}")

            amount = callback_data.get('original_amount', original_payment.amount)
            subscription_type = original_payment.subscription_type
            vpn_type = original_payment.vpn_type

            duration_map = {
                'trial': 1, 'week': 7, 'month': 30, '3months': 90,
                '6months': 180, 'year': 365, '2years': 730,
                'regular_trial': 1, 'regular_day': 1, 'regular_month': 30,
                'regular_3months': 90, 'regular_6months': 180, 'regular_year': 365,
                'regular_2years': 730,
                'fast_trial': 1, 'fast_week': 7, 'fast_month': 30,
                'fast_3months': 90, 'fast_6months': 180, 'fast_year': 365,
            }
            base_type = subscription_type.replace('regular_', '').replace('fast_', '')
            days = duration_map.get(subscription_type, duration_map.get(base_type, 30))

            now = timezone.now()
            last_expires = original_payment.subscription_expires_at
            if last_expires and last_expires > now:
                new_expires = last_expires + timedelta(days=days)
            else:
                new_expires = now + timedelta(days=days)

            # Ищем последний платёж с ключом по этому recurrent_id
            last_with_key = PaymentModel.objects.filter(
                antilopay_recurrent_id=original_payment.antilopay_recurrent_id,
                issued_key__isnull=False,
                status='succeeded'
            ).order_by('-created_at').first()

            if last_with_key:
                # Последующие рекуррентные списания — продлеваем существующий ключ
                new_payment = PaymentModel.objects.create(
                    user=original_payment.user,
                    vpn_type=vpn_type,
                    amount=amount,
                    profit=0,
                    status='pending',
                    subscription_type=subscription_type,
                    antilopay_payment_id=new_payment_id_ap,
                    antilopay_recurrent_id=original_payment.antilopay_recurrent_id,
                    issued_key=last_with_key.issued_key,
                    subscription_expires_at=new_expires,
                    is_renewal=True,
                    renewal_for_payment=last_with_key,
                )
                from .services import PaymentService
                payment_service = PaymentService()
                success = payment_service.confirm_payment(new_payment)
                if not success:
                    logger.error(f"Не удалось продлить ключ для charge #{new_payment.payment_id}")
                    return False
                new_payment.refresh_from_db()
                
                # Сбрасываем трафик в Remnawave при автопродлении
                try:
                    from .remnawave_api import reset_user_traffic_by_short_uuid_sync
                    key_info = last_with_key.issued_key
                    # Извлекаем shortUuid из ключа (последняя часть после последнего /)
                    if '/' in key_info:
                        short_uuid = key_info.split('/')[-1]
                    else:
                        short_uuid = key_info
                    
                    logger.info(f"Сброс трафика для пользователя по ключу {short_uuid}")
                    reset_user_traffic_by_short_uuid_sync(short_uuid)
                    logger.info(f"Трафик успешно сброшен для {short_uuid}")
                except Exception as traffic_error:
                    logger.error(f"Ошибка сброса трафика при продлении: {traffic_error}")
                    # Не прерываем процесс, если сброс трафика не удался
            else:
                # Первый charge после SUBSCRIPTION binding — генерируем ключ
                new_payment = PaymentModel.objects.create(
                    user=original_payment.user,
                    vpn_type=vpn_type,
                    amount=amount,
                    profit=0,
                    status='pending',
                    subscription_type=subscription_type,
                    antilopay_payment_id=new_payment_id_ap,
                    antilopay_recurrent_id=original_payment.antilopay_recurrent_id,
                    subscription_expires_at=new_expires,
                    is_renewal=False,
                )
                from .services import PaymentService
                payment_service = PaymentService()
                success = payment_service.confirm_payment(new_payment)
                if not success:
                    logger.error(f"Не удалось сгенерировать ключ для первого charge #{new_payment.payment_id}")
                    return False
                new_payment.refresh_from_db()

            logger.info(f"Подписка продлена: новый платеж #{new_payment.payment_id}, истекает {new_expires}")

            if not skip_notification:
                AntilopayService._send_recurrent_notification(new_payment, subscription_type, vpn_type, new_expires)

            return True

        except Exception as e:
            logger.error(f"Ошибка обработки рекуррентного платежа: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _handle_payment_success(payment_model: PaymentModel, skip_notification: bool = False) -> bool:
        """Обрабатывает успешный платеж"""
        try:
            from .services import PaymentService

            logger.info(f"Обрабатываем успешный Antilopay платеж: {payment_model.payment_id}")

            vpn_type = getattr(payment_model, 'vpn_type', 'night')
            subscription_type = payment_model.subscription_type

            is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
            is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')

            if is_regular_vpn:
                logger.info(f"Платеж {payment_model.payment_id} - ULTRA FAST VPN, генерируем ключ через Remnawave")
                from .platega_service import PlategaService
                return PlategaService._handle_regular_vpn_payment_success(payment_model, skip_notification=skip_notification)
            elif is_fast_vpn:
                logger.info(f"Платеж {payment_model.payment_id} - Обычный VPN, генерируем ключ через bypass API")
                payment_service = PaymentService()
                return payment_service.confirm_payment(payment_model)
            else:
                logger.info(f"Платеж {payment_model.payment_id} - Night VPN, выдаем ключ через PaymentService")
                payment_service = PaymentService()
                return payment_service.confirm_payment(payment_model)

        except Exception as e:
            logger.error(f"Ошибка обработки успешного Antilopay платежа: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _notify_user(payment_model: PaymentModel) -> bool:
        """Отправляет пользователю ключ в Telegram после успешной оплаты через вебхук"""
        try:
            from config import BOT_TOKEN
            from datetime import timedelta
            from django.utils import timezone
            import json

            user_id = payment_model.user.user_id
            key = payment_model.issued_key
            vpn_type = getattr(payment_model, 'vpn_type', 'night')
            subscription_type = payment_model.subscription_type
            expires_at = payment_model.subscription_expires_at

            if not key or not user_id:
                logger.warning(f"Невозможно отправить уведомление: user_id={user_id}, key={key}")
                return False

            duration_map = {
                'trial': 1, 'week': 7, 'month': 30, '3months': 90,
                '6months': 180, 'year': 365, '2years': 730,
                'regular_trial': 1, 'regular_day': 1, 'regular_month': 30,
                'regular_3months': 90, 'regular_6months': 180, 'regular_year': 365,
                'regular_2years': 730,
                'fast_trial': 1, 'fast_week': 7, 'fast_month': 30,
                'fast_3months': 90, 'fast_6months': 180, 'fast_year': 365,
            }
            base_type = subscription_type.replace('regular_', '').replace('fast_', '')
            days = duration_map.get(subscription_type, duration_map.get(base_type, 30))
            expires_str = (expires_at or (timezone.now() + timedelta(days=days))).strftime('%d.%m.%Y %H:%M')

            if vpn_type == 'night':
                vpn_label = "ОБХОД глушилок + VPN"
                key_button_text = "🛡️ Открыть ключ"
            elif vpn_type == 'regular':
                vpn_label = "ULTRA FAST VPN"
                key_button_text = "⚡ Открыть ключ"
            elif vpn_type == 'fast':
                vpn_label = "Обычный VPN"
                key_button_text = "🚀 Открыть ключ"
            else:
                vpn_label = "VPN"
                key_button_text = "🔑 Открыть ключ"

            message_text = f"""
🎉 <b>Оплата подтверждена!</b>

✅ <b>Подписка {vpn_label} активирована</b>

🔑 <b>Ваш ключ:</b>
{key}

📅 <b>Действует до:</b> {expires_str}

<b>🔧 Как подключить?</b>
1. Нажмите кнопку ниже чтобы открыть ключ
2. Выберите приложение для подключения РЕКОМЕНДУЕМ INCY
3. Нажмите «Добавить подписку»

<i>Спасибо за покупку! 🚀</i>
"""
            reply_markup = json.dumps({
                "inline_keyboard": [
                    [{"text": key_button_text, "url": key}],
                    [
                        {"text": "⬅️ Главное меню", "callback_data": "main_menu"},
                        {"text": "💬 Написать менеджеру", "url": "https://t.me/yamalube61"}
                    ]
                ]
            })

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": message_text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }

            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                logger.info(f"Уведомление отправлено пользователю {user_id}")
                return True
            else:
                logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {response.status_code} {response.text}")
                return False

        except Exception as e:
            logger.error(f"Ошибка в _notify_user: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _send_recurrent_notification(payment: PaymentModel, subscription_type: str, vpn_type: str, expires_at) -> bool:
        """Отправляет уведомление об успешном продлении подписки"""
        try:
            from config import BOT_TOKEN
            import json

            user_id = payment.user.user_id
            key = payment.issued_key

            if not key or not user_id:
                logger.warning(f"Не удалось отправить уведомление о продлении: user_id={user_id}")
                return False

            if vpn_type == 'night':
                vpn_label = "ОБХОД глушилок + VPN"
                key_button_text = "🛡️ Открыть ключ"
            elif vpn_type == 'regular':
                vpn_label = "ULTRA FAST VPN"
                key_button_text = "⚡ Открыть ключ"
            elif vpn_type == 'fast':
                vpn_label = "Обычный VPN"
                key_button_text = "🚀 Открыть ключ"
            else:
                vpn_label = "VPN"
                key_button_text = "🔑 Открыть ключ"

            expires_str = expires_at.strftime('%d.%m.%Y %H:%M') if expires_at else '—'

            message_text = f"""
🔄 <b>Подписка продлена!</b>

✅ <b>{vpn_label} — автопродление</b>

🔑 <b>Ваш ключ:</b>
{key}

📅 <b>Действует до:</b> {expires_str}

<i>Следующее списание — через месяц</i>
"""
            reply_markup = json.dumps({
                "inline_keyboard": [
                    [{"text": key_button_text, "url": key}],
                    [
                        {"text": "⬅️ Главное меню", "callback_data": "main_menu"},
                        {"text": "💬 Написать менеджеру", "url": "https://t.me/yamalube61"}
                    ]
                ]
            })

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": message_text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }

            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                logger.info(f"Уведомление о продлении отправлено пользователю {user_id}")
                return True
            else:
                logger.error(f"Ошибка отправки уведомления о продлении {user_id}: {response.status_code} {response.text}")
                return False

        except Exception as e:
            logger.error(f"Ошибка в _send_recurrent_notification: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
