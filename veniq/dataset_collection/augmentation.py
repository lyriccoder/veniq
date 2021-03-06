import csv
import hashlib
import os
import os.path
import shutil
import tarfile
import typing
from argparse import ArgumentParser
from collections import defaultdict
from functools import partial
from pathlib import Path
from typing import Tuple, Dict, List, Any, Set, Optional

import pandas as pd
from pebble import ProcessPool
from tqdm import tqdm

from veniq.ast_framework import AST, ASTNodeType, ASTNode
from veniq.dataset_collection.types_identifier import AlgorithmFactory, InlineTypesAlgorithms
from veniq.utils.ast_builder import build_ast
from veniq.utils.encoding_detector import read_text_with_autodetected_encoding


def _get_last_line(file_path: Path, start_line: int) -> int:
    """
    This function is aimed to find the last body line of
    considered method. It work by counting the difference
    in number of openning brackets '{' and closing brackets
    '}'. It's start with the method declaration line and going
    to the line where the difference is equal to 0. Which means
    that we found closind bracket of method declaration.
    """
    with open(file_path, encoding='utf-8') as f:
        file_lines = list(f)
        # to start counting opening brackets
        difference_cases = 0

        processed_declaration_line = file_lines[start_line - 1].split('//')[0]
        difference_cases += processed_declaration_line.count('{')
        difference_cases -= processed_declaration_line.count('}')
        for i, line in enumerate(file_lines[start_line:], start_line):
            if difference_cases:
                line_without_comments = line.split('//')[0]
                difference_cases += line_without_comments.count('{')
                difference_cases -= line_without_comments.count('}')
            else:
                return i

        return -1


def get_line_with_first_open_bracket(
    file_path: Path,
    method_decl_start_line: int
) -> int:
    f = open(file_path, encoding='utf-8')
    file_lines = list(f)
    for i, line in enumerate(file_lines[method_decl_start_line - 2:], method_decl_start_line - 2):
        if '{' in line:
            return i + 1
    return method_decl_start_line + 1


def method_body_lines(method_node: ASTNode, file_path: Path) -> Tuple[int, int]:
    """
    Get start and end of method's body
    """
    if len(method_node.body):
        m_decl_start_line = start_line = method_node.line + 1
        start_line = get_line_with_first_open_bracket(file_path, m_decl_start_line)
        end_line = _get_last_line(file_path, start_line)
    else:
        start_line = end_line = -1
    return start_line, end_line


@typing.no_type_check
def is_match_to_the_conditions(
        ast: AST,
        method_invoked: ASTNode,
        found_method_decl=None) -> bool:
    if method_invoked.parent.node_type == ASTNodeType.THIS:
        parent = method_invoked.parent.parent
        class_names = [x for x in method_invoked.parent.children if hasattr(x, 'string')]
        member_references = [x for x in method_invoked.parent.children if hasattr(x, 'member')]
        lst = [x for x in member_references if x.member != method_invoked.member] + class_names
        no_children = not lst
    else:
        parent = method_invoked.parent
        no_children = True

    maybe_if = parent.parent
    is_not_method_inv_single_statement_in_if = True
    if maybe_if.node_type == ASTNodeType.IF_STATEMENT:
        if hasattr(maybe_if.then_statement, 'expression'):
            if maybe_if.then_statement.expression.node_type == ASTNodeType.METHOD_INVOCATION:
                is_not_method_inv_single_statement_in_if = False

    is_not_assign_value_with_return_type = True
    is_not_several_returns = True
    if found_method_decl.return_type:
        if parent.node_type == ASTNodeType.VARIABLE_DECLARATOR:
            is_not_assign_value_with_return_type = False

        ast_subtree = ast.get_subtree(found_method_decl)
        stats = [x for x in ast_subtree.get_proxy_nodes(ASTNodeType.RETURN_STATEMENT)]
        if len(stats) > 1:
            is_not_several_returns = False

    is_not_parent_member_ref = not (method_invoked.parent.node_type == ASTNodeType.MEMBER_REFERENCE)
    is_not_chain_before = not (parent.node_type == ASTNodeType.METHOD_INVOCATION) and no_children
    chains_after = [x for x in method_invoked.children if x.node_type == ASTNodeType.METHOD_INVOCATION]
    is_not_chain_after = not chains_after
    is_not_inside_if = not (parent.node_type == ASTNodeType.IF_STATEMENT)
    is_not_inside_while = not (parent.node_type == ASTNodeType.WHILE_STATEMENT)
    is_not_inside_for = not (parent.node_type == ASTNodeType.FOR_STATEMENT)
    is_not_enhanced_for_control = not (parent.node_type == ASTNodeType.ENHANCED_FOR_CONTROL)
    # ignore case else if (getServiceInterface() != null) {
    is_not_binary_operation = not (parent.node_type == ASTNodeType.BINARY_OPERATION)
    is_not_ternary = not (parent.node_type == ASTNodeType.TERNARY_EXPRESSION)
    # if a parameter is any expression, we ignore it,
    # since it is difficult to extract with AST
    is_actual_parameter_simple = all([hasattr(x, 'member') for x in method_invoked.arguments])
    is_not_class_creator = not (parent.node_type == ASTNodeType.CLASS_CREATOR)
    is_not_cast = not (parent.node_type == ASTNodeType.CAST)
    is_not_array_creator = not (parent.node_type == ASTNodeType.ARRAY_CREATOR)
    is_not_lambda = not (parent.node_type == ASTNodeType.LAMBDA_EXPRESSION)
    other_requirements = all([
        is_not_chain_before,
        is_actual_parameter_simple,
        is_not_chain_after,
        is_not_inside_if,
        is_not_inside_while,
        is_not_binary_operation,
        is_not_ternary,
        is_not_class_creator,
        is_not_cast,
        is_not_array_creator,
        is_not_parent_member_ref,
        is_not_inside_for,
        is_not_enhanced_for_control,
        is_not_lambda,
        is_not_method_inv_single_statement_in_if,
        is_not_assign_value_with_return_type,
        is_not_several_returns,
        not method_invoked.arguments])

    if (not method_invoked.qualifier and other_requirements) or \
            (method_invoked.qualifier == 'this' and other_requirements):
        return True
    else:
        return False


def check_whether_method_has_return_type(
        method_decl: AST,
        var_decls: Set[str]) -> InlineTypesAlgorithms:
    """
    Run function to check whether Method declaration can be inlined
    :param method_decl: method, where invocation occurred
    :param var_decls: set of variables for found invoked method
    :return: enum InlineTypesAlgorithms
    """
    names = get_variables_decl_in_node(method_decl)

    var_decls_original = set(names)
    intersected_names = var_decls & var_decls_original
    # if we do not have intersected name in target method and inlined method
    # and if we do not have var declarations at all
    if not var_decls or not intersected_names:
        return InlineTypesAlgorithms.WITHOUT_RETURN_WITHOUT_ARGUMENTS

    return InlineTypesAlgorithms.DO_NOTHING


def get_variables_decl_in_node(
        method_decl: AST) -> List[str]:
    names = []
    for x in method_decl.get_proxy_nodes(ASTNodeType.VARIABLE_DECLARATOR):
        if hasattr(x, 'name'):
            names.append(x.name)
        elif hasattr(x, 'names'):
            names.extend(x.names)

    for x in method_decl.get_proxy_nodes(ASTNodeType.VARIABLE_DECLARATION):
        if hasattr(x, 'name'):
            names.append(x.name)
        elif hasattr(x, 'names'):
            names.extend(x.names)

    for x in method_decl.get_proxy_nodes(ASTNodeType.TRY_RESOURCE):
        names.append(x.name)

    return names


def determine_algorithm_insertion_type(
        ast: AST,
        method_node: ASTNode,
        invocation_node: ASTNode,
        dict_original_nodes: Dict[str, List[ASTNode]]
) -> InlineTypesAlgorithms:
    """

    :param ast: ast tree
    :param dict_original_nodes: dict with names of function as key
    and list of ASTNode as values
    :param method_node: Method declaration. In this method invocation occurred
    :param invocation_node: invocation node
    :return: InlineTypesAlgorithms enum
    """

    original_invoked_method = dict_original_nodes.get(invocation_node.member, [])
    # ignore overridden functions
    if (len(original_invoked_method) == 0) or (len(original_invoked_method) > 1):
        return InlineTypesAlgorithms.DO_NOTHING
    else:
        original_method = original_invoked_method[0]
        if not original_method.parameters:
            if not original_method.return_type:
                # Find the original method declaration by the name of method invocation
                var_decls = set(get_variables_decl_in_node(ast.get_subtree(original_method)))
                return check_whether_method_has_return_type(
                    ast.get_subtree(method_node),
                    var_decls
                )
            else:
                return InlineTypesAlgorithms.WITH_RETURN_WITHOUT_ARGUMENTS
        else:
            return InlineTypesAlgorithms.DO_NOTHING


def insert_code_with_new_file_creation(
        class_name: str,
        ast: AST,
        method_node: ASTNode,
        invocation_node: ASTNode,
        file_path: Path,
        output_path: Path,
        dict_original_invocations: Dict[str, List[ASTNode]]
) -> List[Any]:
    """
    If invocations of class methods were found,
    we process through all of them and for each
    substitution opportunity by method's body,
    we create new file.
    """
    file_name = file_path.stem
    if not os.path.exists(output_path):
        output_path.mkdir(parents=True)

    new_full_filename = Path(output_path, f'{file_name}_{method_node.name}_{invocation_node.line}.java')
    original_func = dict_original_invocations.get(invocation_node.member)[0]  # type: ignore
    body_start_line, body_end_line = method_body_lines(original_func, file_path)
    text_lines = read_text_with_autodetected_encoding(str(file_path)).split('\n')
    line_to_csv = []
    if body_start_line != body_end_line:
        algorithm_type = determine_algorithm_insertion_type(
            ast,
            method_node,
            invocation_node,
            dict_original_invocations
        )
        algorithm_for_inlining = AlgorithmFactory().create_obj(algorithm_type)
        if algorithm_type != InlineTypesAlgorithms.DO_NOTHING:
            line_to_csv = [
                file_path,
                class_name,
                text_lines[invocation_node.line - 1].lstrip(),
                invocation_node.line,
                original_func.line,
                method_node.name,
                new_full_filename,
                body_start_line,
                body_end_line
            ]

            algorithm_for_inlining().inline_function(
                file_path,
                invocation_node.line,
                body_start_line,
                body_end_line,
                new_full_filename,
            )

    return line_to_csv


def get_ast_if_possibe(file_path: Path) -> Optional[AST]:
    """
    Processing file in order to check
    that its original version can be parsed
    """
    ast = None
    try:
        ast = AST.build_from_javalang(build_ast(str(file_path)))
    except Exception:
        print(f"Processing {file_path} is aborted due to parsing")
    return ast


def analyze_file(file_path: Path, output_path: Path) -> List[Any]:
    """
    In this function we process each file.
    For each file we find each invocation inside,
    which can be inlined.
    """
    results: List[Any] = []
    ast = get_ast_if_possibe(file_path)
    if ast is None:
        return results

    method_declarations = defaultdict(list)
    classes_declaration = [
        ast.get_subtree(node)
        for node in ast.get_root().types
        if node.node_type == ASTNodeType.CLASS_DECLARATION
    ]
    for class_ast in classes_declaration:
        class_declaration = class_ast.get_root()
        for method in class_declaration.methods:
            if not method.parameters:
                method_declarations[method.name].append(method)

        methods_list = list(class_declaration.methods) + list(class_declaration.constructors)
        for method_node in methods_list:
            method_decl = ast.get_subtree(method_node)
            for method_invoked in method_decl.get_proxy_nodes(
                    ASTNodeType.METHOD_INVOCATION):
                found_method_decl = method_declarations.get(method_invoked.member, [])
                # ignore overloaded functions
                if len(found_method_decl) == 1:
                    is_matched = is_match_to_the_conditions(
                        ast,
                        method_invoked,
                        found_method_decl[0]
                    )
                    if is_matched:
                        log_of_inline = insert_code_with_new_file_creation(
                            class_declaration.name,
                            ast,
                            method_node,
                            method_invoked,
                            file_path,
                            output_path,
                            method_declarations)
                        if log_of_inline:
                            results.append(log_of_inline)
    return results


def save_input_file(input_dir: Path, filename: Path) -> Path:
    # need to avoid situation when filenames are the same
    hash_path = hashlib.sha256(str(filename.parent).encode('utf-8')).hexdigest()
    dst_filename = input_dir / f'{filename.stem}_{hash_path}.java'
    if not dst_filename.parent.exists():
        dst_filename.parent.mkdir(parents=True)
    if not dst_filename.exists():
        shutil.copyfile(filename, dst_filename)
    return dst_filename


if __name__ == '__main__':  # noqa: C901
    system_cores_qty = os.cpu_count() or 1
    parser = ArgumentParser()
    parser.add_argument(
        "-d", "--dir", required=True, help="File path to JAVA source code for methods augmentations"
    )
    parser.add_argument(
        "-o", "--output",
        help="Path for file with output results",
        default='augmented_data'
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=system_cores_qty - 1,
        help="Number of processes to spawn. "
             "By default one less than number of cores. "
             "Be careful to raise it above, machine may stop responding while creating dataset.",
    )
    parser.add_argument(
        "-z", "--zip",
        action='store_true',
        help="To zip input and output files."
    )
    parser.add_argument(
        "-s", "--small_dataset_size",
        help="Number of files in small dataset",
        default=100,
        type=int,
    )

    args = parser.parse_args()

    test_files = set(Path(args.dir).glob('**/*Test*.java'))
    not_test_files = set(Path(args.dir).glob('**/*.java'))
    files_without_tests = list(not_test_files.difference(test_files))

    full_dataset_folder = Path(args.output) / 'full_dataset'
    output_dir = full_dataset_folder / 'output_files'
    if not output_dir.exists():
        output_dir.mkdir(parents=True)

    input_dir = full_dataset_folder / 'input_files'
    if not input_dir.exists():
        input_dir.mkdir(parents=True)
    csv_output = Path(full_dataset_folder, 'out.csv')

    with open(csv_output, 'w', newline='\n') as csvfile, ProcessPool(system_cores_qty) as executor:
        writer = csv.writer(
            csvfile, delimiter=',',
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL
        )
        writer.writerow([
            'input filename',
            'className',
            'string where to replace',
            'line where to replace',
            'line of original function',
            'invocation function name',
            'output_filename',
            'start_line',
            'end_line'
        ])

        p_analyze = partial(analyze_file, output_path=output_dir.absolute())
        future = executor.map(p_analyze, files_without_tests, timeout=1000, )
        result = future.result()

        for filename in tqdm(files_without_tests):
            try:
                single_file_features = next(result)
                if single_file_features:
                    for i in single_file_features:
                        dst_filename = save_input_file(input_dir, filename)
                        # change source filename, since it will be chahged
                        i[0] = str(dst_filename.as_posix())
                        #  get local path for inlined filename
                        i[-3] = i[-3].relative_to(os.getcwd()).as_posix()
                        i[2] = str(i[2]).encode('utf8')
                        writer.writerow(i)
                csvfile.flush()
            except StopIteration:
                continue

    if args.zip:
        samples = pd.read_csv(csv_output).sample(args.small_dataset_size, random_state=41)
        small_dataset_folder = Path(args.output) / 'small_dataset'
        if not small_dataset_folder.exists():
            small_dataset_folder.mkdir(parents=True)
        small_input_dir = small_dataset_folder / 'input_files'
        if not small_input_dir.exists():
            small_input_dir.mkdir(parents=True)
        small_output_dir = small_dataset_folder / 'output_files'
        if not small_output_dir.exists():
            small_output_dir.mkdir(parents=True)

        samples.to_csv(small_dataset_folder / 'out.csv')
        for i in samples.iterrows():
            input_filename = i[1]['input filename']
            dst_filename = small_input_dir / Path(input_filename).name
            # print(f"Copy from {input_filename}, to {dst_filename}")
            shutil.copyfile(input_filename, dst_filename)
            output_filename = i[1]['output_filename']
            dst_filename = small_output_dir / Path(output_filename).name
            # print(f"Copy from {output_filename}, to {dst_filename}")
            shutil.copyfile(output_filename, dst_filename)

        with tarfile.open(Path(args.output) / 'small_dataset.tar.gz', "w:gz") as tar:
            tar.add(str(small_dataset_folder), arcname=str(small_dataset_folder))

        with tarfile.open(Path(args.output) / 'full_dataset.tar.gz', "w:gz") as tar:
            tar.add(str(full_dataset_folder), arcname=str(full_dataset_folder))

        if input_dir.exists():
            shutil.rmtree(full_dataset_folder)

        if small_dataset_folder.exists():
            shutil.rmtree(small_dataset_folder)
