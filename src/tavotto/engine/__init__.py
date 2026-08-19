# engine — 参数化渲染引擎
# 注意：本包在两种进程里被使用：
#   * Flask 父进程（.venv，无 matplotlib）只能 import registry / pool
#   * worker 子进程（系统 python3，有科学栈）才 import manifest / overrides / worker
