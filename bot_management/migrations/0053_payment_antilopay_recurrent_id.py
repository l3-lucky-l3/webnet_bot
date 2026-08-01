# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0052_alter_payment_vpn_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='antilopay_recurrent_id',
            field=models.CharField(blank=True, db_index=True, help_text='Идентификатор рекуррентного платежа для автосписания', max_length=255, null=True, verbose_name='ID рекуррента Antilopay'),
        ),
    ]
