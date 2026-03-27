from django.core.management.base import BaseCommand, CommandError

from core.update_runner import run_platform_update


class Command(BaseCommand):
    help = 'Updates CashFlow platform to latest version and writes last_update.log.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--initiated-by',
            default='manual-cli',
            help='Identifier of who triggered the update process.',
        )

    def handle(self, *args, **options):
        initiated_by = options['initiated_by']
        result = run_platform_update(initiated_by=initiated_by)

        if not result['success']:
            raise CommandError(f"Update failed. Check log: {result['log_path']}")

        self.stdout.write(self.style.SUCCESS(f"Update completed. Log: {result['log_path']}"))
