import os
from pyang.context import Context
from pyang.repository import FileRepository


YANG_FILE = "qos-policy.yang"
YANG_DIR = os.path.dirname(os.path.abspath(__file__))

# YANG 트리 구조를 들여쓰기 형태로 출력한다. (node.arg 를 우선 사용)
def print_yang_tree(node, indent=0):
    prefix = "  " * indent
    name = getattr(node, "i_yang_name", None) or getattr(node, "arg", "(unknown)")
    print(f"{prefix}- {node.keyword}: {name}")  # node.keyword: container, list, leaf, module 등 유형

    # 자식 노드를 재귀적으로 출력
    children = getattr(node, "i_children", None)
    if children:
        for child in children:
            print_yang_tree(child, indent + 1)
    else:
        # i_children 가 없으면 substmts 를 순회
        substmts = getattr(node, "substmts", None)
        if substmts:
            for child in substmts:
                print_yang_tree(child, indent + 1)

# YANG 파일을 파싱하고 트리를 출력한 뒤 leaf 이름을 수집한다.
def get_required_policy_keys():
    """
    pyang 으로 qos-policy.yang 을 읽어 policy 리스트 안의 leaf 이름을 모두 추출하고
    모듈의 트리 구조를 콘솔에 출력한다.
    """
    required_keys = set()

    repos = FileRepository(YANG_DIR)
    ctx = Context(repos)
    yang_file_path = os.path.join(YANG_DIR, YANG_FILE)

    # 1단계: YANG 파일을 읽는다. 파일이 없으면 빈 집합을 반환한다.
    try:
        with open(yang_file_path, 'r', encoding='utf-8') as f:
            yang_content = f.read()
    except FileNotFoundError:
        print(f"[YANG PARSER ERROR] File not found: {yang_file_path}")
        return required_keys

    # 2단계: pyang Context로 모듈을 추가하고 검증한다.
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

    # 3단계: 모듈의 substmts를 순회하며 전체 문법 트리를 출력한다.
    print("\n[YANG TREE STRUCTURE]")
    for child in module.substmts:  # substmts 를 사용해 전체 구조를 확인
        print_yang_tree(child, indent=1)

    # 4단계: qos-policies 컨테이너 노드를 찾는다.
    qos_policies_node = next(
        (n for n in module.substmts if getattr(n, 'arg', None) == 'qos-policies'),
        None
    )
    if not qos_policies_node or qos_policies_node.keyword != 'container':
        print("[YANG PARSER WARNING] 'qos-policies' container not found or is not a container.")
        return required_keys

    # 5단계: qos-policies 아래에서 policy 리스트 노드를 찾는다.
    policy_list_node = next(
        (n for n in qos_policies_node.substmts if getattr(n, 'arg', None) == 'policy'),
        None
    )
    if not policy_list_node or policy_list_node.keyword != 'list':
        print("[YANG PARSER WARNING] 'policy' list not found or is not a list.")
        return required_keys

    # 6단계: policy 리스트의 leaf 노드 이름을 모두 수집한다.
    for node in policy_list_node.substmts:
        if node.keyword == 'leaf':
            required_keys.add(node.arg)

    print(f"\n[YANG PARSER] Successfully loaded required keys for 'policy' list: {required_keys}")
    return required_keys


if __name__ == '__main__':
    keys = get_required_policy_keys()
    print("\nExtracted keys:", keys)
