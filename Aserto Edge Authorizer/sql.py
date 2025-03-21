import json


class Union(object):
    def __init__(self, clauses):
        self.clauses = clauses


class InnerJoin(object):
    def __init__(self, tables, expr):
        self.tables = tables
        self.expr = expr

    def sql(self, **kwargs):
        return ' '.join(['INNER JOIN ' + t for t in sorted(self.tables)]) + ' ON ' + self.expr.sql(**kwargs)


class Where(object):
    def __init__(self, expr):
        self.expr = expr

    def sql(self, **kwargs):
        return 'WHERE ' + self.expr.sql(**kwargs)


class Disjunction(object):
    def __init__(self, conjunction):
        self.conjunction = conjunction

    def sql(self, **kwargs):
        return '(' + " OR ".join([c.sql(**kwargs) for c in self.conjunction]) + ')'


class Conjunction(object):
    def __init__(self, relation):
        self.relation = relation

    def sql(self, **kwargs):
        if len(self.relation) == 0:
            return '1'
        return '(' + " AND ".join([r.sql(**kwargs) for r in self.relation]) + ')'


class Relation(object):
    def __init__(self, operator, lhs, rhs):
        self.operator = operator
        self.lhs = lhs
        self.rhs = rhs

    def sql(self, **kwargs):
        return "%s %s %s" % (self.lhs.sql(**kwargs), self.operator.sql(**kwargs), self.rhs.sql(**kwargs))


class Column(object):
    def __init__(self, name, table=''):
        self.table = table
        self.name = name

    def sql(self, **kwargs):
        if self.table:
            return "%s.%s" % (self.table, self.name)
        return str(self.name)


class Call(object):
    def __init__(self, operator, operands):
        self.operator = operator
        self.operands = operands

    def sql(self, **kwargs):
        return self.operator + '(' + ', '.join(o.sql(**kwargs) for o in self.operands) + ')'


class Constant(object):
    def __init__(self, value):
        self.value = value

    def sql(self, **kwargs):
        if kwargs.get('use_single_quotes', False):
            if isinstance(self.value, str):
                return "'" + self.value + "'"
        return json.dumps(self.value)
    

class Array(object):
    def __init__(self, values):
        self.values = values

    def sql(self, **kwargs):
        return '(' + ', '.join(e.sql(**kwargs) for e in self.values) + ')'


class RelationOp(object):
    def __init__(self, value):
        self.value = value

    def sql(self, **kwargs):
        return self.value


def walk(node, vis):
    next = vis(node)
    if next is None:
        return
    if isinstance(node, Union):
        for c in node.clauses:
            walk(c, next)
    elif isinstance(node, Where):
        walk(node.expr, next)
    elif isinstance(node, InnerJoin):
        walk(node.expr, next)
    elif isinstance(node, Disjunction):
        for child in node.conjunction:
            walk(child, next)
    elif isinstance(node, Conjunction):
        for child in node.relation:
            walk(child, next)
    elif isinstance(node, Relation):
        walk(node.operator, next)
        walk(node.lhs, next)
        walk(node.rhs, next)
    elif isinstance(node, Call):
        walk(node.operator, next)
        for o in node.operands:
            walk(o, next)


def pretty_print(node):
    class printer(object):
        def __init__(self, indent):
            self.indent = indent

        def __call__(self, node):
            print(' ' * self.indent, node.__class__.__name__)
            return printer(self.indent + 2)

    vis = printer(0)
    walk(node, vis)
