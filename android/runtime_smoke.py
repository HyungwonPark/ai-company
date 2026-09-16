"""Run the actual signed APK in an empty emulator; never log in or submit approvals."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

PACKAGE = 'cloud.hyungwon.aicompany'


def adb(*args, timeout=20):
    return subprocess.run(['adb', *args], text=True, capture_output=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--emulator-pid', type=int)
    args = parser.parse_args()
    root = args.directory
    apks = list(root.glob('ai-company-*.apk'))
    assert len(apks) == 1
    apk = apks[0]
    record = {'apk': apk.name, 'apk_sha256': hashlib.sha256(apk.read_bytes()).hexdigest(),
              'device_kind': 'Android emulator', 'physical_device': False,
              'login_attempted': False, 'approval_submitted': False}
    try:
        deadline = time.monotonic() + 360
        next_report = time.monotonic()
        while time.monotonic() < deadline:
            if args.emulator_pid is not None and not Path('/proc/' + str(args.emulator_pid)).exists():
                raise RuntimeError('Emulator process exited before Android boot; see emulator.log')
            probe = adb('shell', 'getprop', 'sys.boot_completed')
            (root / 'boot-probe.txt').write_text(probe.stdout + probe.stderr)
            if probe.stdout.strip() == '1':
                break
            if time.monotonic() >= next_report:
                print('Waiting for Android boot; remaining seconds:', int(deadline - time.monotonic()), flush=True)
                next_report = time.monotonic() + 30
            time.sleep(3)
        else:
            raise RuntimeError('Android emulator did not finish booting within 360 seconds')
        record['android_sdk'] = adb('shell', 'getprop', 'ro.build.version.sdk').stdout.strip()
        record['chrome_installed'] = 'com.android.chrome' in adb('shell', 'pm', 'list', 'packages', 'com.android.chrome').stdout
        chrome_version = re.search(r'versionName=([^\s]+)', adb('shell', 'dumpsys', 'package', 'com.android.chrome').stdout)
        record['chrome_version'] = chrome_version[1] if chrome_version else None
        assert record['chrome_installed'], 'The reproduction requires Chrome to be installed'
        adb('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP')
        adb('shell', 'wm', 'dismiss-keyguard')
        previous = list((root / 'previous').glob('ai-company-*.apk'))
        assert len(previous) <= 1
        if previous:
            old = adb('install', str(previous[0]), timeout=90)
            (root / 'previous-install.txt').write_text(old.stdout + old.stderr)
            assert old.returncode == 0 and 'Success' in old.stdout
            record['previous_apk_sha256'] = hashlib.sha256(previous[0].read_bytes()).hexdigest()
        installed = adb('install', '-r', str(apk), timeout=90)
        (root / 'install.txt').write_text(installed.stdout + installed.stderr)
        assert installed.returncode == 0 and 'Success' in installed.stdout
        record['installed'] = True
        record['upgrade_over_010_without_uninstall'] = bool(previous)
        adb('logcat', '-c')
        result = adb('shell', 'am', 'start', '-W', '-a', 'android.intent.action.MAIN',
                     '-c', 'android.intent.category.LAUNCHER', '-n',
                     PACKAGE + '/com.google.androidbrowserhelper.trusted.LauncherActivity')
        (root / 'launch.txt').write_text(result.stdout + result.stderr)
        time.sleep(10)
        crash = adb('logcat', '-b', 'crash', '-d', '-v', 'threadtime').stdout
        (root / 'crash.txt').write_text(crash)
        activity = adb('shell', 'dumpsys', 'activity', 'activities').stdout
        (root / 'activities.txt').write_text(activity)
        (root / 'exit-info.txt').write_text(adb('shell', 'dumpsys', 'activity', 'exit-info', PACKAGE).stdout)
        screenshot = subprocess.run(['adb', 'exec-out', 'screencap', '-p'], capture_output=True, timeout=20)
        if screenshot.returncode == 0:
            (root / 'launch.png').write_bytes(screenshot.stdout)
        record['application_crashed'] = 'Process: ' + PACKAGE in crash
        record['chrome_activity_observed'] = any('com.android.chrome' in line and ('ResumedActivity' in line or 'topResumedActivity' in line) for line in activity.splitlines())
        assert not record['application_crashed'], 'Signed APK crashed; see crash.txt'
        assert result.returncode == 0 and 'Status: ok' in result.stdout
        assert record['chrome_activity_observed'], 'Chrome did not become the resumed activity'
        record['launch_passed'] = True
        # Repeat the same native start path after a background transition and in
        # Android night mode; this does not claim the web theme or login was tested.
        record['repeat_launches'] = []
        for mode in ['no', 'yes']:
            adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
            adb('shell', 'am', 'force-stop', PACKAGE)
            adb('shell', 'cmd', 'uimode', 'night', mode)
            adb('logcat', '-c')
            repeat = adb('shell', 'am', 'start', '-W', '-a', 'android.intent.action.MAIN',
                         '-c', 'android.intent.category.LAUNCHER', '-n',
                         PACKAGE + '/com.google.androidbrowserhelper.trusted.LauncherActivity')
            time.sleep(5)
            repeated_crash = adb('logcat', '-b', 'crash', '-d').stdout
            (root / ('repeat-' + mode + '-crash.txt')).write_text(repeated_crash)
            assert 'Process: ' + PACKAGE not in repeated_crash
            assert repeat.returncode == 0 and 'Status: ok' in repeat.stdout
            active = adb('shell', 'dumpsys', 'activity', 'activities').stdout
            assert any('com.android.chrome' in line and ('ResumedActivity' in line or 'topResumedActivity' in line) for line in active.splitlines())
            record['repeat_launches'].append({'android_night_mode': mode, 'crashed': False,
                                             'chrome_resumed': True})
    except BaseException as error:
        record['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        (root / 'adb-devices.txt').write_text(adb('devices', '-l').stdout)
        (root / 'runtime-result.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record))


if __name__ == '__main__':
    main()
