import importlib.util
import re
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from time import perf_counter

from django.db import connection
from django.db.models import Avg
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from customers.models import BalanceRequest, StoreUserProfile
from inventory.models import Product, ProductReview
from sales.models import Order, Sale

from core.models import SystemTestRun

TEST_IO_RW = 'io_rw'
TEST_DB = 'db'
TEST_REQUIREMENTS = 'requirements'
TEST_DATA_QUALITY = 'data_quality'

SYSTEM_TEST_DEFINITIONS = [
    {
        'key': TEST_IO_RW,
        'title': _lazy('Read/write test'),
        'description': _lazy('Creates and reads temporary files to measure disk speed.'),
    },
    {
        'key': TEST_DB,
        'title': _lazy('Database test'),
        'description': _lazy('Measures connection, query and write/delete operations in database.'),
    },
    {
        'key': TEST_REQUIREMENTS,
        'title': _lazy('System and requirements test'),
        'description': _lazy('Checks minimum requirements needed to run the application.'),
    },
    {
        'key': TEST_DATA_QUALITY,
        'title': _lazy('Data quality test'),
        'description': _lazy('Searches for records with common inconsistencies or unusual data.'),
    },
]

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')


def _check_io_rw_support():
    temp_dir = tempfile.gettempdir()
    path_obj = Path(temp_dir)
    if not path_obj.exists() or not path_obj.is_dir():
        return False, _('Temporary directory not available: %(path)s') % {'path': temp_dir}
    return True, ''


def _check_db_support():
    if connection.vendor not in {'sqlite', 'postgresql', 'mysql'}:
        return False, _('Unsupported DB vendor: %(vendor)s') % {'vendor': connection.vendor}
    return True, ''


def _check_requirements_support():
    return True, ''


def _check_data_quality_support():
    return True, ''


def _run_io_rw_test():
    payload = ('cashflow-io-test-' * 65536).encode('utf-8')
    write_ms = 0.0
    read_ms = 0.0
    verification_ok = False

    with tempfile.NamedTemporaryFile(prefix='cashflow_io_', suffix='.bin', delete=True) as handle:
        start = perf_counter()
        handle.write(payload)
        handle.flush()
        write_ms = (perf_counter() - start) * 1000

        handle.seek(0)
        start = perf_counter()
        read_bytes = handle.read()
        read_ms = (perf_counter() - start) * 1000
        verification_ok = read_bytes == payload

    total_ms = write_ms + read_ms
    status = SystemTestRun.Status.SUCCESS if verification_ok else SystemTestRun.Status.FAIL
    summary = _('Read/write verification OK') if verification_ok else _('Read/write verification mismatch')
    return {
        'status': status,
        'duration_ms': total_ms,
        'summary': summary,
        'details': {
            'bytes': len(payload),
            'write_ms': round(write_ms, 3),
            'read_ms': round(read_ms, 3),
            'verification_ok': verification_ok,
        },
    }


def _run_db_test():
    steps = {}
    test_table = f'cf_system_test_{timezone.now().strftime("%Y%m%d%H%M%S%f")}'

    try:
        with connection.cursor() as cursor:
            start = perf_counter()
            cursor.execute('SELECT 1')
            cursor.fetchone()
            steps['connect_query_ms'] = round((perf_counter() - start) * 1000, 3)

            start = perf_counter()
            cursor.execute(f'CREATE TEMP TABLE {test_table} (id INTEGER PRIMARY KEY, value TEXT)')
            steps['create_table_ms'] = round((perf_counter() - start) * 1000, 3)

            start = perf_counter()
            cursor.execute(f"INSERT INTO {test_table} (value) VALUES (%s)", ['db-test'])
            steps['insert_ms'] = round((perf_counter() - start) * 1000, 3)

            start = perf_counter()
            cursor.execute(f'SELECT COUNT(*) FROM {test_table}')
            count_value = cursor.fetchone()[0]
            steps['select_ms'] = round((perf_counter() - start) * 1000, 3)

            start = perf_counter()
            cursor.execute(f'DROP TABLE {test_table}')
            steps['drop_ms'] = round((perf_counter() - start) * 1000, 3)

        total_ms = round(sum(steps.values()), 3)
        status = SystemTestRun.Status.SUCCESS if count_value == 1 else SystemTestRun.Status.FAIL
        summary = _('Database test completed') if count_value == 1 else _('Database test count mismatch')
        return {
            'status': status,
            'duration_ms': total_ms,
            'summary': summary,
            'details': {'steps_ms': steps, 'row_count': count_value},
        }
    except Exception as exc:
        return {
            'status': SystemTestRun.Status.FAIL,
            'duration_ms': round(sum(value for value in steps.values()), 3),
            'summary': _('Database test failed: %(error)s') % {'error': exc},
            'details': {'steps_ms': steps, 'traceback': traceback.format_exc(limit=5)},
        }


def _run_requirements_test():
    missing = []
    checks = []
    requirements = ['django', 'rest_framework', 'PIL', 'whitenoise']

    for package_name in requirements:
        exists = importlib.util.find_spec(package_name) is not None
        checks.append({'name': package_name, 'ok': exists})
        if not exists:
            missing.append(package_name)

    python_ok = tuple(__import__('sys').version_info[:2]) >= (3, 10)
    checks.append({'name': 'python>=3.10', 'ok': python_ok})
    if not python_ok:
        missing.append('python>=3.10')

    status = SystemTestRun.Status.SUCCESS if not missing else SystemTestRun.Status.FAIL
    return {
        'status': status,
        'duration_ms': 0.0,
        'summary': _('Requirements OK') if not missing else _('Missing requirements: %(missing)s') % {'missing': ', '.join(missing)},
        'details': {
            'checked_at': datetime.utcnow().isoformat() + 'Z',
            'checks': checks,
            'missing': missing,
        },
    }


def _detect_control_chars(value):
    if not value:
        return False
    return bool(_CONTROL_CHARS_RE.search(str(value)))


def _run_data_quality_test():
    findings = []

    products_negative_stock = Product.objects.filter(stock__lt=0).count()
    if products_negative_stock:
        findings.append(_('Products with negative stock: %(count)s') % {'count': products_negative_stock})

    products_zero_price = Product.objects.filter(price__lte=0).count()
    if products_zero_price:
        findings.append(_('Products with price <= 0: %(count)s') % {'count': products_zero_price})

    approved_orders_without_items = Order.objects.filter(status=Order.Status.APPROVED, items__isnull=True).count()
    if approved_orders_without_items:
        findings.append(_('Approved orders without items: %(count)s') % {'count': approved_orders_without_items})

    active_sales_without_items = Sale.objects.filter(is_voided=False, items__isnull=True).count()
    if active_sales_without_items:
        findings.append(_('Active sales without items: %(count)s') % {'count': active_sales_without_items})

    bad_rating_count = ProductReview.objects.exclude(rating__gte=1, rating__lte=5).count()
    if bad_rating_count:
        findings.append(_('Reviews with invalid rating: %(count)s') % {'count': bad_rating_count})

    sample_product_names = list(Product.objects.values_list('name', flat=True)[:100])
    weird_product_names = sum(1 for name in sample_product_names if _detect_control_chars(name))
    if weird_product_names:
        findings.append(_('Products with suspicious characters: %(count)s') % {'count': weird_product_names})

    profiles_without_user = StoreUserProfile.objects.filter(user__isnull=True).count()
    if profiles_without_user:
        findings.append(_('Profiles without user relation: %(count)s') % {'count': profiles_without_user})

    pending_balance_with_negative = BalanceRequest.objects.filter(status=BalanceRequest.Status.PENDING, amount__lte=0).count()
    if pending_balance_with_negative:
        findings.append(
            _('Pending balance requests with non-positive amount: %(count)s') % {'count': pending_balance_with_negative}
        )

    status = SystemTestRun.Status.SUCCESS if not findings else SystemTestRun.Status.FAIL
    return {
        'status': status,
        'duration_ms': 0.0,
        'summary': _('No common data issues detected.') if not findings else _('Found %(count)s potential issues.') % {'count': len(findings)},
        'details': {'findings': findings},
    }


_SUPPORT_CHECKERS = {
    TEST_IO_RW: _check_io_rw_support,
    TEST_DB: _check_db_support,
    TEST_REQUIREMENTS: _check_requirements_support,
    TEST_DATA_QUALITY: _check_data_quality_support,
}

_RUNNERS = {
    TEST_IO_RW: _run_io_rw_test,
    TEST_DB: _run_db_test,
    TEST_REQUIREMENTS: _run_requirements_test,
    TEST_DATA_QUALITY: _run_data_quality_test,
}


def _trend_for_runs(test_key, runs):
    if test_key not in {TEST_IO_RW, TEST_DB}:
        return {'label': '', 'kind': 'none'}

    durations = [float(run.duration_ms) for run in runs if run.status == SystemTestRun.Status.SUCCESS and run.duration_ms]
    if len(durations) < 2:
        return {'label': _('No data'), 'kind': 'none'}

    latest = durations[0]
    baseline = sum(durations[1:]) / max(len(durations[1:]), 1)
    if latest <= baseline * 0.95:
        return {'label': _('Improves'), 'kind': 'improve'}
    if latest >= baseline * 1.05:
        return {'label': _('Worsens'), 'kind': 'worsen'}
    return {'label': _('Stable'), 'kind': 'stable'}


def run_system_test(test_key, *, executed_by=None, save_result=True):
    if test_key not in _RUNNERS:
        raise ValueError(_('Unknown test key: %(key)s') % {'key': test_key})

    supported, support_note = _SUPPORT_CHECKERS[test_key]()
    if not supported:
        result = {
            'test_key': test_key,
            'supported': False,
            'status': SystemTestRun.Status.SKIPPED,
            'duration_ms': None,
            'summary': support_note or _('Test not supported in current environment.'),
            'details': {'support_note': support_note},
        }
    else:
        payload = _RUNNERS[test_key]()
        result = {
            'test_key': test_key,
            'supported': True,
            'status': payload['status'],
            'duration_ms': payload.get('duration_ms'),
            'summary': payload.get('summary', ''),
            'details': payload.get('details', {}),
        }

    if not save_result:
        return result

    return SystemTestRun.objects.create(
        test_type=test_key,
        supported=result['supported'],
        status=result['status'],
        duration_ms=result['duration_ms'],
        summary=result['summary'],
        details=result['details'],
        executed_by=executed_by,
    )


def build_system_tests_overview():
    overview = []
    for definition in SYSTEM_TEST_DEFINITIONS:
        key = definition['key']
        supported, support_note = _SUPPORT_CHECKERS[key]()
        recent_runs = list(SystemTestRun.objects.filter(test_type=key).order_by('-created_at')[:10])
        latest_run = recent_runs[0] if recent_runs else None
        trend = _trend_for_runs(key, recent_runs)
        avg_duration = (
            SystemTestRun.objects.filter(test_type=key, status=SystemTestRun.Status.SUCCESS, duration_ms__isnull=False)
            .aggregate(value=Avg('duration_ms'))
            .get('value')
        )
        overview.append(
            {
                'key': key,
                'title': definition['title'],
                'description': definition['description'],
                'supported': supported,
                'support_note': support_note,
                'latest_run': latest_run,
                'recent_runs': recent_runs,
                'trend': trend,
                'avg_duration': avg_duration,
            }
        )
    return overview
