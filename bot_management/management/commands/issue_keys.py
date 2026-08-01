from django.core.management.base import BaseCommand
from bot_management.models import Payment
from bot_management.services import PaymentService


class Command(BaseCommand):
    help = 'Выдает ключи для всех оплаченных платежей без ключей'

    def handle(self, *args, **options):
        # Находим все оплаченные платежи без ключей
        payments = Payment.objects.filter(
            status='succeeded',
            issued_key__isnull=True
        )
        
        self.stdout.write(f'Найдено {payments.count()} платежей без ключей')
        
        payment_service = PaymentService()
        success_count = 0
        
        for payment in payments:
            try:
                success = payment_service.confirm_payment(payment)
                if success:
                    payment.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✅ Платеж {payment.payment_id}: ключ {payment.issued_key}'
                        )
                    )
                    success_count += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f'❌ Платеж {payment.payment_id}: ошибка выдачи ключа'
                        )
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ Платеж {payment.payment_id}: {e}'
                    )
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Выдано ключей: {success_count} из {payments.count()}'
            )
        )
