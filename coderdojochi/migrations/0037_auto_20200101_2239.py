# Generated by Django 2.2.9 on 2020-01-02 04:39

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('coderdojochi', '0036_auto_20200101_2238'),
    ]

    operations = [
        migrations.RenameField(
            model_name='equipment',
            old_name='aquisition_date',
            new_name='acquisition_date',
        ),
    ]