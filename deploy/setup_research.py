"""Install the independent table compute interpreter, without touching API deps."""
import argparse
import pathlib
import subprocess

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--python',default='python3.12');parser.add_argument('--index-url')
    args=parser.parse_args();root=pathlib.Path(__file__).resolve().parents[1];venv=root/'runtime/research-python'
    subprocess.check_call([args.python,'-m','venv',str(venv)])
    command=[str(venv/'bin/python'),'-m','pip','install','-r',str(root/'deploy/requirements-research.txt')]
    if args.index_url:command+=['--index-url',args.index_url]
    subprocess.check_call(command)

if __name__=='__main__':main()
