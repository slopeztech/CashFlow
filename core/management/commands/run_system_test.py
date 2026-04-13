import json

from django.core.management.base import BaseCommand, CommandError

from core.system_tests import (
    SYSTEM_TEST_DEFINITIONS,
    build_system_tests_overview,
    run_system_test,
)


class Command(BaseCommand):
    help = 'Run system tests and optionally persist execution logs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            dest='test_type',
            default='all',
            help='Test key to run: io_rw, db, requirements, data_quality, all',
        )
        parser.add_argument(
            '--no-save',
            action='store_true',
            help='Run tests without saving logs to database.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Print output in JSON format.',
        )
        parser.add_argument(
            '--overview',
            action='store_true',
            help='Show current test overview from stored logs.',
        )

    def handle(self, *args, **options):
        if options['overview']:
            overview = build_system_tests_overview()
            if options['json']:
                payload = [
                    {
                        'key': item['key'],
                        'supported': item['supported'],
                        'latest_status': item['latest_run'].status if item['latest_run'] else None,
                        'latest_summary': item['latest_run'].summary if item['latest_run'] else '',
                        'runs': len(item['recent_runs']),
                    }
                    for item in overview
                ]
                self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                for item in overview:
                    latest = item['latest_run']
                    latest_status = latest.status if latest else 'N/A'
                    latest_summary = latest.summary if latest else 'No runs yet.'
                    self.stdout.write(
                        f"[{item['key']}] supported={item['supported']} "
                        f"status={latest_status} | {latest_summary}"
                    )
            return

        valid_keys = {item['key'] for item in SYSTEM_TEST_DEFINITIONS}
        requested = (options['test_type'] or 'all').strip().lower()
        if requested == 'all':
            selected_keys = [item['key'] for item in SYSTEM_TEST_DEFINITIONS]
        elif requested in valid_keys:
            selected_keys = [requested]
        else:
            raise CommandError(
                f"Unknown test type '{requested}'. "
                f"Valid: {', '.join(sorted(valid_keys))}, all"
            )

        results = []
        save_result = not options['no_save']
        for key in selected_keys:
            result = run_system_test(key, save_result=save_result)
            if hasattr(result, 'test_type'):
                payload = {
                    'test_type': result.test_type,
                    'supported': result.supported,
                    'status': result.status,
                    'duration_ms': float(result.duration_ms) if result.duration_ms is not None else None,
                    'summary': result.summary,
                    'created_at': result.created_at.isoformat(),
                }
            else:
                payload = result
            results.append(payload)

        if options['json']:
            self.stdout.write(json.dumps(results, ensure_ascii=False, indent=2, default=str))
            return

        for item in results:
            self.stdout.write(
                f"[{item['test_type']}] supported={item['supported']} "
                f"status={item['status']} duration_ms={item['duration_ms']} "
                f"| {item['summary']}"
            )
