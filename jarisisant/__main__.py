import argparse

from .app import main

parser = argparse.ArgumentParser(prog="jarisisant")
parser.add_argument("--smoke", action="store_true", help="открыть окно и сразу закрыть (проверка запуска)")
main(smoke=parser.parse_args().smoke)
