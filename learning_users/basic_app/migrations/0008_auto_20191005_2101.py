# Generated by Django 2.2.4 on 2019-10-05 21:01

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('basic_app', '0007_auto_20191005_2058'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parttask',
            name='user',
            field=models.ForeignKey(on_delete='Do_Nothing', to=settings.AUTH_USER_MODEL),
        ),
    ]