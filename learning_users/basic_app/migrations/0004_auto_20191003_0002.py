# Generated by Django 2.2.4 on 2019-10-03 00:02

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('basic_app', '0003_auto_20191003_0001'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parttask',
            name='time_stop',
            field=models.DateTimeField(default=None),
        ),
    ]
