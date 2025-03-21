"""
Example library for calling the OPA service and then translating the result into SQL.
Source: https://github.com/open-policy-agent/contrib/tree/main/data_filter_example
"""

import requests
import shutil
import tempfile
import os
import subprocess
import json
from collections import namedtuple
from rego import ast, walk
import sql


class TranslationError(Exception):
    """Raised if an error occurs during the Rego to SQL translation."""

    pass


class Result(object):
    """Represents the result of a compile call.

    Attributes:

        defined (bool): If the query is NEVER defined, defined is False. In
        this case, the app can interpret the result as denying the request.

        sql (:class:`sql.Union`): If the query is ALWAYS defined, sql is None.
        In this case the app can interpret the result as allowing the request
        unconditionally. If sql is not None, the app should apply the SQL
        clauses to the query it is about to run.
    """

    def __init__(self, defined, sql):
        self.defined = defined
        self.sql = sql


def compile_http(query, input, unknowns):
    """Returns a set of compiled queries."""
    # print('calling OPA service via HTTP')
    response = requests.post(
        "http://localhost:8181/v1/compile",
        data=json.dumps(
            {
                "query": query,
                "input": input,
                "unknowns": unknowns,
            }
        ),
    )
    body = response.json()
    if response.status_code != 200:
        raise Exception("%s: %s" % (body.code, body.message))
    return body.get("result", {}).get("queries", [])

def evaluate(input):
    # print('calling OPA service via HTTP')
    response = requests.post(
        "http://localhost:8181/v1/data/example/allow",
        data=json.dumps(
            {
                "input": input
            }
        ),
    )
    # print('response:', response.json())
    body = response.json()
    if response.status_code != 200:
        raise Exception("%s: %s" % (body.code, body.message))
    return body.get("result", {})


def compile_command_line(data_files):
    """Returns a function that can be called to compile a query using OPA's eval subcommand."""

    def wrapped(query, input, unknowns):
        args = ["opa", "eval", "--partial", "--format", "json"]
        for u in unknowns:
            args.extend(["--unknowns", u])
        dirpath = tempfile.mkdtemp()
        try:
            data_dirpath = os.path.join(dirpath, "data")
            os.makedirs(data_dirpath)
            for filename, content in data_files.items():
                with open(os.path.join(data_dirpath, filename), "w") as f:
                    f.write(content)
            args.extend(["--data", data_dirpath])
            if input is not None:
                input_path = os.path.join(dirpath, "input.json")
                with open(input_path, "w") as f:
                    json.dump(input, f)
                args.extend(["--input", input_path])
            args.append(query)
            output = subprocess.check_output(args, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise Exception(
                "exit code %d: command: %s: %s" % (e.returncode, e.cmd, e.output)
            )
        finally:
            shutil.rmtree(dirpath)
        return json.loads(output).get("partial", {}).get("queries", [])

    return wrapped


def compile(q, input, unknowns, from_table=None, compile_func=None):
    """Returns a :class:`Result` that can be interpreted by the app to enforce
    the policy."""

    if compile_func is None:
        compile_func = compile_http

    queries = compile_func(
        query=q, input=input, unknowns=["data." + u for u in unknowns]
    )

    # Check if query is never or always defined.
    if len(queries) == 0:
        return Result(False, None)
    elif any((len(x) == 0 for x in queries)):
        return Result(True, None)

    query_set = ast.QuerySet.from_data(queries)
    queryPreprocessor().process(query_set)
    clauses = queryTranslator(from_table).translate(query_set)

    return Result(True, clauses)

def generate_queries(body, from_table=None):
    queries = body.get("result", {}).get("queries", [])

    if len(queries) == 0:
        return Result(False, None)
    elif any((len(x) == 0 for x in queries)):
        return Result(True, None)

    query_set = ast.QuerySet.from_data(queries)
    queryPreprocessor().process(query_set)
    clauses = queryTranslator(from_table).translate(query_set)

    return Result(True, clauses)


def splice(SELECT, FROM, WHERE="", decision=None, sql_kwargs=None):
    """Returns a SQL query as a string constructed from the caller's provided
    values and the decision returned by compile."""
    sql = "SELECT " + SELECT + " FROM " + FROM
    if decision is not None and decision.sql is not None:
        queries = [sql] * len(decision.sql.clauses)
        for i, clause in enumerate(decision.sql.clauses):
            if sql_kwargs is None:
                sql_kwargs = {}
            queries[i] = queries[i] + " " + clause.sql(**sql_kwargs)
            if WHERE:
                queries[i] = queries[i] + " AND (" + WHERE + ")"
    return " UNION ".join(queries)

class queryTranslator(object):
    """Implements the vistor pattern to translate Rego queries into equivalent
    SQL clauses."""

    # Maps supported Rego relational operators to SQL relational operators.
    _sql_relation_operators = {
        "internal.member_2": "in",
        "eq": "=",
        "equal": "=",
        "neq": "!=",
        "lt": "<",
        "gt": ">",
        "lte": "<=",
        "gte": ">=",
    }

    # Maps supported Rego call operators to SQL call operators.
    _sql_call_operators = {
        "abs": "abs",
    }

    def __init__(self, from_table):
        self._from_table = from_table
        self._joins = []
        self._conjunctions = []
        self._tables = set([])
        self._relations = []
        self._operands = []

    def translate(self, query_set):
        """Returns a :class:`sql.Union` containing :class:`sql.Where` and
        :class:`sql.InnerJoin` clauses to be applied to the query."""
        walk.walk(query_set, self)
        clauses = []
        if len(self._conjunctions) > 0:
            clauses = [
                sql.Where(sql.Disjunction([conj for conj in self._conjunctions]))
            ]
        for tables, conj in self._joins:
            pred = sql.InnerJoin(tables, conj)
            clauses.append(pred)
        return sql.Union(clauses)

    def __call__(self, node):
        if isinstance(node, ast.Query):
            self._translate_query(node)
        elif isinstance(node, ast.Expr):
            if not node.ignore:
                self._translate_expr(node)
        elif isinstance(node, ast.Term):
            self._translate_term(node)
        else:
            return self

    def _translate_query(self, node):
        """Pushes an expression onto the conjunction or join stack if multiple
        tables are referred to."""
        for expr in node.exprs:
            walk.walk(expr, self)
        conj = sql.Conjunction(self._relations)
        if len(self._tables) > 1:
            self._tables.remove(self._from_table)
            self._joins.append((self._tables, conj))
        else:
            self._conjunctions.append(conj)
        self._tables = set([])
        self._relations = []

    def _translate_expr(self, node):
        """Pushes an element onto the relation stack."""
        if not node.is_call():
            return
        if len(node.operands) != 2:
            raise TranslationError("invalid expression: too many arguments")
        try:
            op = node.op()
            sql_op = sql.RelationOp(self._sql_relation_operators[op])
        except KeyError:
            raise TranslationError(
                "invalid expression: operator not supported: %s" % op
            )
        self._operands.append([])
        for term in node.operands:
            walk.walk(term, self)
        sql_operands = self._operands.pop()
        self._relations.append(sql.Relation(sql_op, *sql_operands))

    def _translate_term(self, node):
        """Pushes an element onto the operand stack."""
        v = node.value
        if isinstance(v, ast.Scalar):
            self._operands[-1].append(sql.Constant(v.value))
        elif isinstance(v, ast.Ref) and len(v.terms) == 3:
            table = v.terms[1].value.value
            self._tables.add(table)
            col = sql.Column(v.terms[2].value.value, table)
            self._operands[-1].append(col)
        elif isinstance(v, ast.Call):
            try:
                op = v.op()
                sql_op = self._sql_call_operators[op]
            except KeyError:
                raise TranslationError("invalid call: operator not supported: %s" % op)
            self._operands.append([])
            for term in v.operands:
                walk.walk(term, self)
            sql_operands = self._operands.pop()
            self._operands[-1].append(sql.Call(sql_op, sql_operands))
        elif isinstance(v, ast.Array):
            self._operands[-1].append(
                sql.Array([sql.Constant(t.value.value) for t in v.terms])
            )
        else:
            raise TranslationError(
                "invalid term: type not supported: %s" % v.__class__.__name__
            )

class queryPreprocessor(object):
    """Implements the visitor pattern to preprocess refs in the Rego query set.
    Preprocessing the Rego query set simplifies the translation process.

    Refs are rewritten to correspond directly to SQL tables and columns.
    Specifically, refs of the form data.foo[var].bar are rewritten as
    data.foo.bar. Similarly, if var is dereferenced later in the query, e.g.,
    var.baz, that will be rewritten as data.foo.baz."""

    def __init__(self):
        self._table_names = []
        self._table_vars = {}

    def process(self, query_set):
        walk.walk(query_set, self)

    def __call__(self, node):
        if isinstance(node, ast.Query):
            self._table_names.append({})
            self._table_vars = {}
        elif isinstance(node, ast.Expr):
            node.ignore = False
            if node.is_call():
                if node.op() == "eq":
                    # Filter out temp variable expressions, resulting from multiple `some` statements
                    # TODO make cleaner
                    all_variables = True
                    for o in node.operands:
                        if not isinstance(o.value, ast.Ref):
                            all_variables = False
                        else:
                            if isinstance(o.value.operand(-1).value, ast.Scalar):
                                all_variables = False
                            if (
                                isinstance(o.value.operand(-1).value, ast.Var)
                                and "__local" not in o.value.operand(-1).value.value
                            ):
                                all_variables = False
                    if all_variables:
                        # Throw out expression
                        node.ignore = True
                        return None
                # Skip the built-in call operator.
                for o in node.operands:
                    walk.walk(o, self)
                self.cur_operator = None
                return
        elif isinstance(node, ast.Call):
            # Skip the call operator.
            for o in node.operands:
                walk.walk(o, self)
            return
        elif isinstance(node, ast.Ref):
            head = node.terms[0].value.value

            if head in self._table_vars:
                # Expand ref in case head was an intermediate var. E.g.,
                # "data.foo[x]; x.bar" => "data.foo[x]; data.foo.bar".
                node.terms = self._table_vars[head] + node.terms[1:]
                return

            row_id = node.terms[2].value

            # Refs must be of the form data.<table>[<iterator>].<column>.
            if not isinstance(row_id, ast.Var):
                raise TranslationError(
                    "invalid reference: row identifier type not supported: %s"
                    % row_id.__class__.__name__
                )

            prefix = node.terms[:2]

            # Add mapping so that we can expand refs above.
            self._table_vars[row_id.value] = prefix
            table_name = node.terms[1].value.value

            # Keep track of iterators used for each table. We do not support
            # self-joins currently. Self-joins require namespacing in the SQL
            # query.

            exist = self._table_names[-1].get(table_name, row_id.value)
            if exist != row_id.value:
                raise TranslationError("invalid reference: self-joins not supported")
            else:
                self._table_names[-1][table_name] = row_id.value

            # Rewrite ref to remove iterator var. E.g., "data.foo[x].bar" =>
            # "data.foo.bar".
            node.terms = prefix + node.terms[3:]
            return

        return self
