import os
from pyang.context import Context
from pyang.repository import FileRepository


YANG_FILE = "qos-policy.yang"
YANG_DIR = os.path.dirname(os.path.abspath(__file__))


# Print the YANG tree structure with indentation (preferring node.arg)
def print_yang_tree(node, indent=0):
    prefix = "  " * indent
    name = getattr(node, "i_yang_name", None) or getattr(node, "arg", "(unknown)")
    print(f"{prefix}- {node.keyword}: {name}")  # node.keyword examples: container, list, leaf, module

    # Recursively print child nodes
    children = getattr(node, "i_children", None)
    if children:
        for child in children:
            print_yang_tree(child, indent + 1)
    else:
        # Fall back to substmts when i_children is absent
        substmts = getattr(node, "substmts", None)
        if substmts:
            for child in substmts:
                print_yang_tree(child, indent + 1)


# Parse the YANG file, print the tree, and collect leaf names under the policy list
def get_required_policy_keys():
    """
    Read qos-policy.yang with pyang, print the module tree, and collect the leaf names
    inside the 'policy' list. Returns a set of required keys.
    """
    required_keys = set()

    repos = FileRepository(YANG_DIR)
    ctx = Context(repos)
    yang_file_path = os.path.join(YANG_DIR, YANG_FILE)

    # Step 1: Read YANG file. Return empty set if missing.
    try:
        with open(yang_file_path, 'r', encoding='utf-8') as f:
            yang_content = f.read()
    except FileNotFoundError:
        print(f"[YANG PARSER ERROR] File not found: {yang_file_path}")
        return required_keys

    # Step 2: Add module to pyang Context and validate.
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

    # Step 3: Traverse substatements to print full grammar tree.
    print("\n[YANG TREE STRUCTURE]")
    for child in module.substmts:  # Use substmts to inspect the entire structure
        print_yang_tree(child, indent=1)

    # Step 4: Find the qos-policies container node.
    qos_policies_node = next(
        (n for n in module.substmts if getattr(n, 'arg', None) == 'qos-policies'),
        None
    )
    if not qos_policies_node or qos_policies_node.keyword != 'container':
        print("[YANG PARSER WARNING] 'qos-policies' container not found or is not a container.")
        return required_keys

    # Step 5: Find the policy list node under qos-policies.
    policy_list_node = next(
        (n for n in qos_policies_node.substmts if getattr(n, 'arg', None) == 'policy'),
        None
    )
    if not policy_list_node or policy_list_node.keyword != 'list':
        print("[YANG PARSER WARNING] 'policy' list not found or is not a list.")
        return required_keys

    # Step 6: Collect leaf node names inside the policy list.
    for node in policy_list_node.substmts:
        if node.keyword == 'leaf':
            required_keys.add(node.arg)

    print(f"\n[YANG PARSER] Successfully loaded required keys for 'policy' list: {required_keys}")
    return required_keys


if __name__ == '__main__':
    keys = get_required_policy_keys()
    print("\nExtracted keys:", keys)
