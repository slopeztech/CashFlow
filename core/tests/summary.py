import atexit


_SECTIONS = []


def register_section(name, tests_count=None, details=None):
    _SECTIONS.append(
        {
            'name': name,
            'tests_count': tests_count,
            'details': details or {},
        }
    )


def _render_value(value):
    if value is None:
        return '-'
    return str(value)


def _print_summary_table():
    if not _SECTIONS:
        return

    name_width = max(len(section['name']) for section in _SECTIONS)
    count_width = max(len(_render_value(section['tests_count'])) for section in _SECTIONS)

    print('\nTest sections summary')
    print(f"{'Section'.ljust(name_width)} | {'Tests'.rjust(count_width)}")
    print(f"{'-' * name_width}-+-{'-' * count_width}")

    for section in _SECTIONS:
        print(f"{section['name'].ljust(name_width)} | {_render_value(section['tests_count']).rjust(count_width)}")

    for section in _SECTIONS:
        if not section['details']:
            continue
        print(f"\n{section['name']} details")
        for key, value in section['details'].items():
            print(f"- {key}: {value}")


atexit.register(_print_summary_table)
