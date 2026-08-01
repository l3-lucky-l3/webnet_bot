import logging
from django.utils import timezone
from django.db.models import Sum
from .referral_models import ReferralCode, Referral, ReferralReward
from .models import TelegramUser, Payment, SubscriptionKey
import random
import string
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

class ReferralService:
    """Сервис для работы с реферальной системой"""
    
    def __init__(self, bot=None):
        self.bot = bot
    
    def create_referral_code_sync(self, user_id: int) -> dict:
        """Создание реферального кода для пользователя (синхронная версия)"""
        try:
            user = TelegramUser.objects.get(user_id=user_id)
            
            # Проверяем, есть ли уже код
            try:
                referral_code = ReferralCode.objects.get(user=user)
                return {
                    'success': True,
                    'code': referral_code.code,
                    'created': False,
                    'message': f'Ваш реферальный код: {referral_code.code}'
                }
            except ReferralCode.DoesNotExist:
                # Создаем новый код
                referral_code = ReferralCode.objects.create(
                    user=user,
                    is_active=True
                )
                return {
                    'success': True,
                    'code': referral_code.code,
                    'created': True,
                    'message': f'Ваш реферальный код: {referral_code.code}'
                }
            
        except Exception as e:
            logger.error(f"Ошибка создания реферального кода: {e}")
            return {
                'success': False,
                'message': 'Ошибка создания реферального кода'
            }
    
    async def create_referral_code(self, user_id: int) -> dict:
        """Создание реферального кода для пользователя"""
        try:
            from asgiref.sync import sync_to_async
            
            user = await sync_to_async(TelegramUser.objects.get)(user_id=user_id)
            referral_code, created = await sync_to_async(ReferralCode.objects.get_or_create)(
                user=user,
                defaults={'is_active': True}
            )
            
            return {
                'success': True,
                'code': referral_code.code,
                'created': created,
                'message': f'Ваш реферальный код: {referral_code.code}'
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания реферального кода: {e}")
            return {
                'success': False,
                'message': 'Ошибка создания реферального кода'
            }
    
    def process_referral(self, referrer_code: str, referred_user_id: int) -> dict:
        """Обработка реферала"""
        try:
            # Получаем или создаем пользователя
            referred_user, created = TelegramUser.objects.get_or_create(
                user_id=referred_user_id,
                defaults={
                    'first_entry_method': 'referral',  # Устанавливаем способ входа как реферальный
                    'multi_level_referral_enabled': False
                }
            )
            
            # Если пользователь уже существует, проверяем способ его первого входа
            if not created:
                logger.info(f"DEBUG: Пользователь {referred_user_id} уже существует, first_entry_method={referred_user.first_entry_method}")
                
                # Если пользователь уже был приглашен по реферальной программе, не создаем дубликат
                existing_referral = Referral.objects.filter(referred=referred_user).first()
                if existing_referral:
                    # Но все равно возвращаем успех, чтобы не показывать ошибку
                    referrer = existing_referral.referrer
                    logger.info(f"DEBUG: Пользователь {referred_user_id} уже приглашен пользователем {referrer.user_id}")
                    return {
                        'success': True,
                        'referrer': referrer,
                        'message': f'Вы уже приглашены пользователем @{referrer.username or referrer.first_name}'
                    }
                
                # Если пользователь уже заходил в бота не через реферальную ссылку
                # Но еще не был приглашен - разрешаем создать реферальную связь
                # (убираем блокировку для пользователей с first_entry_method == 'direct')
                logger.info(f"DEBUG: Пользователь {referred_user_id} еще не был приглашен, создаем реферальную связь")
            
            # Проверяем, что пользователь не реферал сам себя
            if ReferralCode.objects.filter(user_id=referred_user_id, code=referrer_code).exists():
                return {
                    'success': False,
                    'message': 'Нельзя использовать свой реферальный код'
                }
            
            # Находим код реферера
            try:
                referrer_code_obj = ReferralCode.objects.get(code=referrer_code, is_active=True)
                referrer = referrer_code_obj.user
            except ReferralCode.DoesNotExist:
                return {
                    'success': False,
                    'message': 'Реферальный код не найден'
                }
            
            # Проверяем, не был ли уже зарегистрирован этот реферал с этим реферером
            if Referral.objects.filter(referred=referred_user, referrer=referrer).exists():
                # Безопасная обработка username для сообщения
                referrer_display = referrer.username if referrer.username else (referrer.first_name if referrer.first_name else f"ID{referrer.user_id}")
                referrer_display = f"@{referrer_display}" if referrer.username else referrer_display
                return {
                    'success': True,
                    'referrer': referrer,
                    'message': f'Вы уже приглашены пользователем {referrer_display}'
                }
            
            # ВАЖНО: Проверяем, что у реферера есть реферальный код (должен быть, так как мы его нашли выше)
            # Но на всякий случай проверяем, что пользователь существует
            if not referrer:
                logger.error(f"DEBUG: Реферер не найден для кода {referrer_code}")
                return {
                    'success': False,
                    'message': 'Ошибка: реферер не найден'
                }
            
            # Создаем реферальную связь
            # ВАЖНО: Реферальная связь создается независимо от наличия username у реферера
            # Username используется только для отображения в сообщениях, но не влияет на создание связи
            from django.db import transaction
            with transaction.atomic():
                # Обновляем способ входа пользователя только если он еще не установлен
                if not referred_user.first_entry_method:
                    referred_user.first_entry_method = 'referral'
                    referred_user.save()
                
                referral = Referral.objects.create(
                    referrer=referrer,
                    referred=referred_user,
                    is_active=True
                )
                logger.info(f"DEBUG: Создана реферальная связь: реферер {referrer.user_id} (username={referrer.username or 'нет'}) -> реферал {referred_user.user_id}")
                
                # Награда будет создана только при покупке приглашенного
            
            # Безопасная обработка username для сообщения
            referrer_display = referrer.username if referrer.username else (referrer.first_name if referrer.first_name else f"ID{referrer.user_id}")
            referrer_display = f"@{referrer_display}" if referrer.username else referrer_display
            return {
                'success': True,
                'referrer': referrer,
                'message': f'Вы приглашены пользователем {referrer_display}'
            }
            
        except Exception as e:
            logger.error(f"Ошибка обработки реферала: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'message': f'Ошибка обработки реферала: {str(e)}'
            }
    
    def get_referral_stats_sync(self, user_id: int) -> dict:
        """Получение статистики рефералов (синхронная версия) - как на сайте"""
        try:
            # Получаем пользователя, если не найден - создаем с базовыми значениями
            try:
                user = TelegramUser.objects.get(user_id=user_id)
            except TelegramUser.DoesNotExist:
                logger.warning(f"Пользователь {user_id} не найден при получении статистики, создаем")
                user, created = TelegramUser.objects.get_or_create(
                    user_id=user_id,
                    defaults={
                        'username': None,
                        'first_name': None,
                        'last_name': None,
                        'balance': 0,
                        'referral_balance': 0,
                        'multi_level_referral_enabled': False
                    }
                )
            
            # Количество рефералов
            referrals_count = Referral.objects.filter(referrer=user, is_active=True).count()
            
            # Получаем всех рефералов с оптимизацией запроса
            referrals = Referral.objects.filter(referrer=user, is_active=True).select_related('referred')
            referred_user_ids = [ref.referred.user_id for ref in referrals] if referrals.exists() else []
            
            # Количество покупок рефералов - учитываем все успешные платежи (succeeded)
            # Включая те, которые были подтверждены через админку
            # Оптимизация: объединяем запросы для уменьшения количества обращений к БД
            from django.db.models import Sum, Count
            total_purchases = 0
            total_revenue = 0
            if referred_user_ids:
                # Получаем и количество, и сумму одним запросом
                stats = Payment.objects.filter(
                    user_id__in=referred_user_ids,
                    status='succeeded'
                ).aggregate(
                    total_purchases=Count('payment_id'),
                    total_revenue=Sum('amount')
                )
                total_purchases = stats['total_purchases'] or 0
                total_revenue = stats['total_revenue'] or 0
            
            # Общая комиссия (20% от выручки) - считаем все награды, не только со статусом 'paid'
            total_commission = 0
            try:
                total_commission_result = ReferralReward.objects.filter(
                    referral__referrer=user
                ).aggregate(total=Sum('reward_value'))
                total_commission = total_commission_result['total'] or 0
            except Exception as e:
                logger.warning(f"Ошибка получения комиссии для пользователя {user_id}: {e}")
            
            # Реферальный код (создаем если нет)
            try:
                referral_code = ReferralCode.objects.get(user=user, is_active=True)
                code = referral_code.code
            except ReferralCode.DoesNotExist:
                # Создаем код если его нет
                try:
                    referral_code = ReferralCode.objects.create(
                        user=user,
                        is_active=True
                    )
                    code = referral_code.code
                    logger.info(f"Создан реферальный код {code} для пользователя {user_id}")
                except Exception as e:
                    logger.error(f"Ошибка создания реферального кода для пользователя {user_id}: {e}")
                    code = 'Не создан'
            
            return {
                'success': True,
                'referrals_count': referrals_count,
                'total_purchases': total_purchases,
                'total_revenue': float(total_revenue),
                'total_commission': float(total_commission),
                'total_rewards': float(total_commission),  # Для обратной совместимости
                'referral_code': code,
                'referral_balance': float(user.referral_balance),
                'commission_percent': 20
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики рефералов для пользователя {user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Возвращаем базовые значения вместо ошибки
            return {
                'success': True,  # Возвращаем success=True, чтобы не ломать загрузку профиля
                'referrals_count': 0,
                'total_purchases': 0,
                'total_revenue': 0.0,
                'total_commission': 0.0,
                'total_rewards': 0.0,
                'referral_code': 'Не создан',
                'referral_balance': 0.0,
                'commission_percent': 20
            }
    
    async def get_referral_stats(self, user_id: int) -> dict:
        """Получение статистики рефералов - как на сайте"""
        try:
            from asgiref.sync import sync_to_async
            
            user = await sync_to_async(TelegramUser.objects.get)(user_id=user_id)
            
            # Количество рефералов
            referrals_count = await sync_to_async(Referral.objects.filter(referrer=user, is_active=True).count)()
            
            # Получаем всех рефералов
            referrals = await sync_to_async(list)(Referral.objects.filter(referrer=user, is_active=True))
            referred_user_ids = [ref.referred.user_id for ref in referrals]
            
            # Количество покупок рефералов
            total_purchases = await sync_to_async(Payment.objects.filter(
                user_id__in=referred_user_ids,
                status='succeeded'
            ).count)()
            
            # Общая выручка от покупок рефералов
            from django.db.models import Sum
            total_revenue_result = await sync_to_async(Payment.objects.filter(
                user_id__in=referred_user_ids,
                status='succeeded'
            ).aggregate)(total=Sum('amount'))
            total_revenue = total_revenue_result['total'] or 0
            
            # Общая комиссия (20% от выручки) - считаем все награды, не только со статусом 'paid'
            total_commission_result = await sync_to_async(ReferralReward.objects.filter(
                referral__referrer=user
            ).aggregate)(total=Sum('reward_value'))
            total_commission = total_commission_result['total'] or 0
            
            # Реферальный код
            try:
                referral_code = await sync_to_async(ReferralCode.objects.get)(user=user, is_active=True)
                code = referral_code.code
            except ReferralCode.DoesNotExist:
                code = None
            
            return {
                'success': True,
                'referrals_count': referrals_count,
                'total_purchases': total_purchases,
                'total_revenue': float(total_revenue),
                'total_commission': float(total_commission),
                'total_rewards': total_commission,  # Для обратной совместимости
                'referral_code': code,
                'referral_balance': float(user.referral_balance),
                'commission_percent': 20
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики рефералов: {e}")
            return {
                'success': False,
                'message': 'Ошибка получения статистики'
            }
    
    async def process_referral_purchase(self, user_id: int, payment: Payment) -> dict:
        """Обработка покупки реферала - автоматическая выплата 20%"""
        try:
            logger.info(f"DEBUG: Обработка реферальной покупки для пользователя {user_id}, сумма: {payment.amount}")
            
            # Находим реферальную связь
            try:
                referral = await sync_to_async(Referral.objects.get)(referred_id=user_id, is_active=True)
                logger.info(f"DEBUG: Найдена реферальная связь: {referral.referrer.user_id} -> {referral.referred.user_id}")
            except Referral.DoesNotExist:
                logger.info(f"DEBUG: Реферальная связь не найдена для пользователя {user_id}")
                return {'success': True, 'message': 'Реферал не найден'}
            
            # Рассчитываем награду (20% от суммы покупки)
            reward_amount = int(payment.amount * 20 / 100)  # 20% от покупки
            logger.info(f"DEBUG: Рассчитана награда: {reward_amount} ₽ (20% от {payment.amount})")
            
            # Создаем награду
            reward = await sync_to_async(ReferralReward.objects.create)(
                referral=referral,
                reward_type='percent',
                reward_value=reward_amount,
                status='paid',  # Сразу помечаем как выплаченную
                paid_at=timezone.now()
            )
            
            # Начисляем на реферальный баланс реферера
            referrer = referral.referrer
            old_referral_balance = referrer.referral_balance
            referrer.referral_balance += reward_amount
            await sync_to_async(referrer.save)()
            logger.info(f"DEBUG: Реферальный баланс реферера {referrer.user_id} обновлен: {old_referral_balance} -> {referrer.referral_balance}")
            
            # Создаем транзакцию реферального баланса
            from .models import ReferralBalanceTransaction
            # Безопасная обработка username для описания транзакции
            user_display = payment.user.username if payment.user.username else f"ID{payment.user.user_id}"
            await sync_to_async(ReferralBalanceTransaction.objects.create)(
                user=referrer,
                transaction_type='referral_reward',
                amount=reward_amount,
                description=f'Реферальная награда 20% за покупку пользователя {user_display}'
            )
            
            # Уведомляем реферера
            if self.bot:
                try:
                    # Безопасная обработка username для уведомления
                    username = payment.user.username if payment.user.username else None
                    first_name = payment.user.first_name if payment.user.first_name else None
                    
                    if username:
                        user_display = f"@{username}"
                    elif first_name:
                        user_display = first_name
                    else:
                        user_display = f"ID{payment.user.user_id}"
                    
                    message = f"""
🎉 <b>Реферальная награда!</b>

💰 <b>Начислено на реферальный баланс:</b> {reward_amount} ₽
👤 <b>За покупку:</b> {user_display}
💳 <b>Ваш реферальный баланс:</b> {referrer.referral_balance} ₽

💡 <b>Продолжайте приглашать друзей и получайте награды!</b>
📤 <b>Вы можете запросить вывод средств через бота</b>
"""
                    await self.bot.send_message(referral.referrer.user_id, message, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления рефереру: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            return {
                'success': True,
                'reward_amount': reward_amount,
                'message': f'Награда {reward_amount} ₽ автоматически начислена на баланс реферера'
            }
            
        except Exception as e:
            logger.error(f"Ошибка обработки покупки реферала: {e}")
            return {
                'success': False,
                'message': 'Ошибка обработки реферала'
            }
    
    def process_referral_purchase_sync(self, user_id: int, payment) -> dict:
        """Синхронная обработка покупки реферала - автоматическая выплата 20%"""
        try:
            logger.info(f"DEBUG: Синхронная обработка реферальной покупки для пользователя {user_id}, сумма: {payment.amount}, payment_id: {payment.payment_id}")
            
            # Находим реферальную связь
            try:
                referral = Referral.objects.get(referred_id=user_id, is_active=True)
                logger.info(f"DEBUG: Найдена реферальная связь: реферер {referral.referrer.user_id} -> реферал {referral.referred.user_id}")
            except Referral.DoesNotExist:
                logger.info(f"DEBUG: Реферальная связь не найдена для пользователя {user_id}")
                return {'success': True, 'message': 'Реферал не найден'}
            except Referral.MultipleObjectsReturned:
                # Если несколько реферальных связей, берем первую активную
                logger.warning(f"DEBUG: Найдено несколько реферальных связей для пользователя {user_id}, берем первую")
                referral = Referral.objects.filter(referred_id=user_id, is_active=True).first()
                if not referral:
                    logger.info(f"DEBUG: Активная реферальная связь не найдена для пользователя {user_id}")
                    return {'success': True, 'message': 'Реферал не найден'}
            
            # Проверяем, не была ли уже начислена награда за этот платеж
            # Используем поле payment в ReferralReward для связи с платежом
            from .referral_models import ReferralReward
            existing_reward = ReferralReward.objects.filter(
                referral=referral,
                payment=payment
            ).first()
            
            if existing_reward:
                logger.info(f"DEBUG: Награда за платеж {payment.payment_id} уже была начислена ранее (reward_id={existing_reward.id})")
                return {'success': True, 'message': 'Награда уже начислена'}
            
            # Рассчитываем награду (20% от суммы покупки)
            reward_amount = int(payment.amount * 20 / 100)  # 20% от покупки
            logger.info(f"DEBUG: Рассчитана награда: {reward_amount} ₽ (20% от {payment.amount})")
            
            # ВАЖНО: Начисление происходит независимо от наличия username или бота
            # Создаем награду с привязкой к платежу
            from .referral_models import ReferralReward
            reward = ReferralReward.objects.create(
                referral=referral,
                reward_type='percent',
                reward_value=reward_amount,
                status='paid',  # Сразу помечаем как выплаченную
                paid_at=timezone.now(),
                payment=payment  # Привязываем к платежу для проверки дубликатов
            )
            logger.info(f"DEBUG: Создана награда {reward.id} для реферала {referral.id}, payment_id={payment.payment_id}")
            
            # Начисляем на реферальный баланс реферера
            referrer = referral.referrer
            old_referral_balance = referrer.referral_balance
            referrer.referral_balance += reward_amount
            referrer.save()
            logger.info(f"DEBUG: Реферальный баланс реферера {referrer.user_id} обновлен: {old_referral_balance} -> {referrer.referral_balance}")
            
            # Создаем транзакцию реферального баланса
            from .models import ReferralBalanceTransaction
            # Безопасная обработка username для описания транзакции (не влияет на начисление)
            try:
                user_display = payment.user.username if payment.user and payment.user.username else f"ID{payment.user.user_id if payment.user else user_id}"
            except:
                user_display = f"ID{user_id}"
            
            ReferralBalanceTransaction.objects.create(
                user=referrer,
                transaction_type='referral_reward',
                amount=reward_amount,
                description=f'Реферальная награда 20% за покупку пользователя {user_display}'
            )
            logger.info(f"DEBUG: Создана транзакция реферального баланса для реферера {referrer.user_id}")
            
            # Уведомляем реферера (если есть бот)
            if self.bot:
                try:
                    # Определяем тип подписки для сообщения
                    sub_type_names = {
                        'month': '📅 Месячная подписка',
                        '3months': '📅 3 месяца',
                        '6months': '📅 6 месяцев',
                        'year': '📅 Годовая подписка',
                        'lifetime': '🔓 Пожизненная подписка'
                    }
                    sub_type_text = sub_type_names.get(payment.subscription_type, payment.subscription_type)
                    
                    # Имя пользователя - безопасная обработка None
                    username = payment.user.username if payment.user.username else None
                    first_name = payment.user.first_name if payment.user.first_name else None
                    
                    if username:
                        user_name = f"@{username}"
                    elif first_name:
                        user_name = first_name
                    else:
                        user_name = f"ID{payment.user.user_id}"
                    
                    message = f"""
🎉 <b>Реферальная награда!</b>

👤 <b>Ваш реферал совершил покупку:</b>
• Пользователь: {user_name}
• Подписка: {sub_type_text}
• Сумма покупки: {payment.amount} ₽

💰 <b>Начислено на реферальный баланс:</b> {reward_amount} ₽ (20%)
💳 <b>Ваш реферальный баланс:</b> {referrer.referral_balance} ₽

💡 <b>Продолжайте приглашать друзей и получайте награды!</b>
📤 <b>Вы можете запросить вывод средств через бота</b>
"""
                    # Отправляем уведомление синхронно через requests
                    import requests
                    from config import BOT_TOKEN
                    
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    data = {
                        'chat_id': referrer.user_id,
                        'text': message,
                        'parse_mode': 'HTML'
                    }
                    response = requests.post(url, data=data, timeout=5)
                    
                    if response.status_code == 200:
                        logger.info(f"DEBUG: Уведомление рефереру {referrer.user_id} отправлено успешно")
                    else:
                        logger.error(f"DEBUG: Ошибка отправки уведомления рефереру {referrer.user_id}: {response.status_code} - {response.text}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления рефереру: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            return {
                'success': True,
                'reward_amount': reward_amount,
                'message': f'Награда {reward_amount} ₽ автоматически начислена на баланс реферера'
            }
            
        except Exception as e:
            logger.error(f"Ошибка синхронной обработки реферальной покупки: {e}")
            return {
                'success': False,
                'message': 'Ошибка сервера'
            }
