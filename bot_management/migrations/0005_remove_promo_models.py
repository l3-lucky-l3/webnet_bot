# Generated manually to remove promo models

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0004_promocode_referral_referralcode_referralreward_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='PromoCode',
        ),
        migrations.DeleteModel(
            name='PromoUsage',
        ),
    ]
