import os
from pyang.context import Context
from pyang.repository import FileRepository


YANG_FILE = "qos-policy.yang"
YANG_DIR = os.path.dirname(os.path.abspath(__file__))

# 递归打印 YANG 节点树 (使用 node.arg 替代 i_yang_name)
def print_yang_tree(node, indent=0):
    prefix = "  " * indent
    name = getattr(node, "i_yang_name", None) or getattr(node, "arg", "(unknown)")
    print(f"{prefix}- {node.keyword}: {name}") # node.keyword：节点类型（如 "container", "list", "leaf", "module" 等）

    # 打印子节点
    children = getattr(node, "i_children", None)
    if children:
        for child in children:
            print_yang_tree(child, indent + 1)
    else:
        # 如果还没进行语义绑定，则尝试 substmts
        substmts = getattr(node, "substmts", None)
        if substmts:
            for child in substmts:
                print_yang_tree(child, indent + 1)

# 解析 YANG 文件 → 打印结构 → 提取 leaf 名称。
def get_required_policy_keys():
    """
    使用 pyang 解析 qos-policy.yang 文件，提取 'policy' 列表中的所有 leaf 节点名称，
    并打印整个模块的语法树结构。
    """
    required_keys = set()

    repos = FileRepository(YANG_DIR)
    ctx = Context(repos)
    yang_file_path = os.path.join(YANG_DIR, YANG_FILE)

    # --- 1. 读取文件 ---
    try:
        with open(yang_file_path, 'r', encoding='utf-8') as f:
            yang_content = f.read()
    except FileNotFoundError:
        print(f"[YANG PARSER ERROR] File not found: {yang_file_path}")
        return required_keys

    # --- 2. 解析模块 ---
    try:
        module = ctx.add_module(YANG_FILE, yang_content)
        ctx.validate()
        print(f"module = {module}")
    except Exception as e:
        print(f"[YANG PARSER ERROR] Could not parse module: {e}")
        return required_keys

    if not module:
        print(f"[YANG PARSER ERROR] Module {YANG_FILE} is empty.")
        return required_keys

    # --- 3. 打印整个语法树 ---
    print("\n[YANG TREE STRUCTURE]")
    for child in module.substmts:  # 改为 substmts 确保结构完整
        print_yang_tree(child, indent=1)

    # --- 4. 查找 container qos-policies ---
    qos_policies_node = next(
        (n for n in module.substmts if getattr(n, 'arg', None) == 'qos-policies'), # 如果找到第一个匹配的，就返回该节点，否则返回 None
        None
    )
    if not qos_policies_node or qos_policies_node.keyword != 'container':
        print("[YANG PARSER WARNING] 'qos-policies' container not found or is not a container.")
        return required_keys

    # --- 5. 查找 list policy ---
    policy_list_node = next(
        (n for n in qos_policies_node.substmts if getattr(n, 'arg', None) == 'policy'),
        None
    )
    if not policy_list_node or policy_list_node.keyword != 'list':
        print("[YANG PARSER WARNING] 'policy' list not found or is not a list.")
        return required_keys

    # --- 6. 提取 leaf 名称 ---
    for node in policy_list_node.substmts:
        if node.keyword == 'leaf':
            required_keys.add(node.arg)

    print(f"\n[YANG PARSER] Successfully loaded required keys for 'policy' list: {required_keys}")
    return required_keys


if __name__ == '__main__':
    keys = get_required_policy_keys()
    print("\nExtracted keys:", keys)
