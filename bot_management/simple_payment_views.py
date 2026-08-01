"""
Упрощенные API endpoints для работы с платежами
"""
import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .simple_payment_service import SimplePaymentService

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
def create_simple_payment(request):
    """Создать простой платеж для пополнения баланса"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = float(data.get('amount', 0))
        
        if not user_id or amount <= 0:
            return JsonResponse({'success': False, 'message': 'Неверные параметры'}, status=400)
        
        # Создаем платеж в ЮKassa
        payment_data = SimplePaymentService.create_payment(
            user_id=user_id,
            amount=amount,
            description=f'Пополнение баланса на {amount} ₽'
        )
        
        if payment_data:
            return JsonResponse({
                'success': True,
                'payment_id': payment_data['payment_id'],
                'confirmation_url': payment_data['confirmation_url'],
                'status': payment_data['status'],
                'amount': payment_data['amount']
            })
        else:
            return JsonResponse({'success': False, 'message': 'Ошибка создания платежа'}, status=500)
            
    except Exception as e:
        logger.error(f"Ошибка создания простого платежа: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def check_simple_payment_status(request, payment_id):
    """Проверить статус простого платежа"""
    try:
        # Проверяем статус в ЮKassa
        yookassa_data = SimplePaymentService.check_payment_status(payment_id)
        
        if not yookassa_data:
            return JsonResponse({'success': False, 'message': 'Платеж не найден'}, status=404)
        
        # Если платеж успешен, обрабатываем его
        if yookassa_data['status'] == 'succeeded' and yookassa_data['paid']:
            # Получаем user_id из метаданных (если есть) или из параметров
            user_id = request.GET.get('user_id')
            if user_id:
                success = SimplePaymentService.process_payment_success(
                    payment_id=payment_id,
                    user_id=int(user_id),
                    amount=float(yookassa_data['amount'])
                )
                
                if success:
                    return JsonResponse({
                        'success': True,
                        'status': 'succeeded',
                        'message': 'Платеж успешно обработан',
                        'amount': yookassa_data['amount']
                    })
        
        return JsonResponse({
            'success': True,
            'status': yookassa_data['status'],
            'paid': yookassa_data['paid'],
            'amount': yookassa_data['amount']
        })
        
    except Exception as e:
        logger.error(f"Ошибка проверки статуса платежа {payment_id}: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def process_payment_webhook(request):
    """Обработать webhook от ЮKassa"""
    try:
        webhook_data = json.loads(request.body)
        event = webhook_data.get('event')
        payment_object = webhook_data.get('object', {})
        
        logger.info(f"Получен webhook: {event} для платежа {payment_object.get('id')}")
        
        if event == 'payment.succeeded':
            payment_id = payment_object.get('id')
            metadata = payment_object.get('metadata', {})
            user_id = metadata.get('user_id')
            amount = payment_object.get('amount', {}).get('value')
            
            if user_id and amount:
                success = SimplePaymentService.process_payment_success(
                    payment_id=payment_id,
                    user_id=int(user_id),
                    amount=float(amount)
                )
                
                if success:
                    logger.info(f"Платеж {payment_id} успешно обработан через webhook")
                    return JsonResponse({'status': 'ok', 'message': 'Платеж обработан'})
        
        return JsonResponse({'status': 'ok', 'message': 'Webhook получен'})
        
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)






