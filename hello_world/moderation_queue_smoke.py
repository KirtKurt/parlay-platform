import json
import sys
import time
import urllib.error
import urllib.request

API_URL = sys.argv[1].rstrip('/') + '/'
MEMBER_ID = 'smoke-member'


def request(method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(API_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8')
            return resp.status, json.loads(body or '{}')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8')
        raise AssertionError(f'{method} {path} failed {exc.code}: {body}')


def main():
    status, dashboard_before = request('GET', 'v1/inqsi/moderation/dashboard')
    assert status == 200 and dashboard_before.get('ok') is True, dashboard_before
    before_total = int((dashboard_before.get('summary') or {}).get('total_uploads') or 0)
    before_manual = int((dashboard_before.get('summary') or {}).get('manual_review') or 0)

    stamp = str(int(time.time()))
    clean_id = f'smoke-clean-{stamp}'
    text_id = f'smoke-text-{stamp}'
    manual_id = f'smoke-manual-{stamp}'

    clean_payload = {
        'upload_id': clean_id,
        'member_id': MEMBER_ID,
        'filename': 'clean-smoke.png',
        'image_role': 'profile',
        'media_type': 'image/png',
        'sha256': clean_id,
        'file_size_bytes': 1,
        'external_scan_result': {'imageModeration': {'decision': 'APPROVED', 'reason': 'passed_inqsi_image_policy', 'manual_review_required': False}},
    }
    status, clean = request('POST', 'v1/inqsi/member-images/upload', clean_payload)
    assert status == 200 and clean.get('ok') is True, clean
    assert clean['upload']['moderation_status'] == 'approved', clean
    assert clean['upload']['is_visible'] is True, clean

    text_payload = {
        'upload_id': text_id,
        'member_id': MEMBER_ID,
        'filename': 'text-smoke.png',
        'image_role': 'post',
        'media_type': 'image/png',
        'sha256': text_id,
        'file_size_bytes': 1,
        'external_scan_result': {'imageModeration': {'decision': 'REJECTED', 'reason': 'reject_if_any_readable_text_detected', 'manual_review_required': False}},
    }
    status, text = request('POST', 'v1/inqsi/member-images/upload', text_payload)
    assert status == 200 and text.get('ok') is True, text
    assert text['upload']['moderation_status'] == 'rejected', text
    assert text['upload']['is_visible'] is False, text

    manual_payload = {
        'upload_id': manual_id,
        'member_id': MEMBER_ID,
        'filename': 'manual-smoke.png',
        'image_role': 'banner',
        'media_type': 'image/png',
        'sha256': manual_id,
        'file_size_bytes': 1,
        'external_scan_result': {'imageModeration': {'decision': 'MANUAL_REVIEW', 'reason': 'pending_admin_review', 'manual_review_required': True}},
    }
    status, manual = request('POST', 'v1/inqsi/member-images/upload', manual_payload)
    assert status == 200 and manual.get('ok') is True, manual
    assert manual['upload']['moderation_status'] == 'manual_review', manual

    status, queue = request('GET', 'v1/inqsi/moderation/queue?status=manual_review&member_id=smoke-member&limit=200')
    assert status == 200 and queue.get('ok') is True, queue
    assert any(item.get('upload_id') == manual_id for item in queue.get('items', [])), queue

    status, dashboard_after = request('GET', 'v1/inqsi/moderation/dashboard')
    assert status == 200 and dashboard_after.get('ok') is True, dashboard_after
    after_total = int((dashboard_after.get('summary') or {}).get('total_uploads') or 0)
    after_manual = int((dashboard_after.get('summary') or {}).get('manual_review') or 0)
    assert after_total >= before_total + 3, {'before': before_total, 'after': after_total, 'dashboard': dashboard_after}
    assert after_manual >= before_manual + 1, {'before_manual': before_manual, 'after_manual': after_manual, 'dashboard': dashboard_after}

    print(json.dumps({
        'ok': True,
        'tested': [
            'dashboard_opened',
            'clean_image_uploaded_and_approved',
            'text_image_uploaded_and_rejected',
            'manual_review_queue_updated',
            'dashboard_counts_updated',
        ],
        'upload_ids': [clean_id, text_id, manual_id],
        'before_total': before_total,
        'after_total': after_total,
        'before_manual': before_manual,
        'after_manual': after_manual,
    }, indent=2))


if __name__ == '__main__':
    main()
