"""Small authenticated onboarding client. No implicit activation or grants.

python -m enterprise_data_platform.admin_cli --base-url https://rdh.example \
  --token-file /secure/admin-token preview --file clinical-preview.json --output clinical-draft.json
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, quote

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ['register-source', 'preview', 'validate', 'index-plan', 'save-dataset']:
        item = sub.add_parser(command)
        item.add_argument('--file', type=Path, required=True)
        item.add_argument('--output', type=Path)
        if command == 'save-dataset': item.add_argument('--expected-version')
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('base URL must be credential-free HTTPS')
    token = args.token_file.read_text().strip()
    if not token or len(token) > 32768: parser.error('invalid token file')
    body = json.loads(args.file.read_text())
    if args.command != 'preview': body = body.get('dataset', body)
    routes = {'preview': ('POST','/v1/control/onboarding/preview'),
              'validate': ('POST','/v1/control/onboarding/validate'),
              'index-plan': ('POST','/v1/control/onboarding/index-plan')}
    if args.command == 'register-source':
        method,path='PUT','/v1/control/sources/'+quote(body['id'],safe='')
    elif args.command == 'save-dataset':
        method,path='PUT','/v1/control/datasets/'+quote(body['id'],safe='')
    else: method,path=routes[args.command]
    params = {'expected_version':args.expected_version} if getattr(args,'expected_version',None) else None
    with httpx.Client(base_url=args.base_url.rstrip('/'),headers={'Authorization':'Bearer '+token},
                      timeout=60,follow_redirects=False,trust_env=False) as client:
        response=client.request(method,path,json=body,params=params)
    if response.status_code >= 300:
        # Never print submitted payloads, bearer tokens or raw HTTP debug output.
        try: detail=response.json().get('detail','request failed')
        except ValueError: detail='request failed'
        parser.exit(1,f'HTTP {response.status_code}: {detail}\n')
    result=response.json()
    if args.command=='preview': result=result['dataset']
    rendered=json.dumps(result,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(rendered)
        print(str(args.output))
    else: print(rendered,end='')


if __name__=='__main__': main()
