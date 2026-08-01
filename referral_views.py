from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from .referral_services import ReferralService
from .models import TelegramUser, Payment
import logging

def staff_required(view_func):
    """Декоратор для проверки прав администратора"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return HttpResponseForbidden("Доступ запрещен. Требуются права администратора.")
        return view_func(request, *args, **kwargs)
    return wrapper

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def create_referral_code(request):
    """API для создания реферального кода"""
    try:
        user_id = int(request.POST.get('user_id', 0))
        
        if not user_id:
            return JsonResponse({
                'success': False,
                'message': 'Неверные параметры'
            }, status=400)
        
        referral_service = ReferralService()
        result = referral_service.create_referral_code_sync(user_id)
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Ошибка создания реферального кода: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка сервера'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def process_referral(request):
    """API для обработки реферала"""
    try:
        referrer_code = request.POST.get('referrer_code', '').strip()
        referred_user_id = int(request.POST.get('referred_user_id', 0))
        
        if not referrer_code or not referred_user_id:
            return JsonResponse({
                'success': False,
                'message': 'Неверные параметры'
            }, status=400)
        
        referral_service = ReferralService()
        result = referral_service.process_referral(referrer_code, referred_user_id)
        
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Ошибка обработки реферала: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка сервера'
        }, status=500)


@require_http_methods(["GET"])
def get_referral_stats(request, user_id):
    """API для получения статистики рефералов"""
    try:
        referral_service = ReferralService()
        result = referral_service.get_referral_stats_sync(user_id)
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики рефералов: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка сервера'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def process_referral_purchase(request):
    """API для обработки покупки реферала"""
    try:
        user_id = int(request.POST.get('user_id', 0))
        payment_id = int(request.POST.get('payment_id', 0))
        
        if not user_id or not payment_id:
            return JsonResponse({
                'success': False,
                'message': 'Неверные параметры'
            }, status=400)
        
        try:
            payment = Payment.objects.get(payment_id=payment_id, user_id=user_id)
        except Payment.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Платеж не найден'
            }, status=404)
        
        import asyncio
        
        async def process_purchase():
            referral_service = ReferralService()
            return await referral_service.process_referral_purchase(user_id, payment)
        
        # Запускаем асинхронную функцию в новом event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(process_purchase())
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки реферала: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка сервера'
        }, status=500)
