"""
A set of tools which allow specifying a model consisting of sheets
organized in levels, and projections connecting these sheets. The
sheets have an attribute matchconditions allowing to specify which
other (incoming) sheets a sheet should connect to.

Instances of the LabelDecorator decorator are offered for setting
parameters/matchconditions for a sheet within a level, as well as
parameters for projections.
"""

import itertools

from functools import wraps
from collections import OrderedDict

import param
import lancet
import topo
import numbergen

from dataviews.collector import AttrTree
from topo.misc.commandline import global_params



def select(index, *decorators):
    """
    A meta-decorator that applies one-of-N possible decorators based
    on the index. The index may be a boolean value for selecting
    between two options.

    """
    def wrapped(*args, **kwargs):
        return decorators[int(index)](*args, **kwargs)
    return wrapped



def order_projections(model, connection_order):
    """
    Helper function for reproducing random streams when
    time_dependent=False (where the order of projection instantiation
    matters). This function should only be used for legacy reasons and
    should not be used with new models.

    The first argument is an instance of Model (with projections
    configured) and the second is the projection ordering, specified
    by the (decorated) method names generating those projections.

    This function allows sorting on a single source sheet property. To
    specify such an ordering, use a tuple where the first element is
    the relevant method name and the second element is a source sheet
    property dictionary to match. For instance, specifying the
    connection_order list as:

    [('V1_afferent_projections', {'polarity':'On'}),
    ('V1_afferent_projections',  {'polarity':'Off'})]

    will order the 'On' projections generated by the
    V1_afferent_projections method before the 'Off' projections.
    """
    connection_list = [el if isinstance(el, tuple) else (el, None)
                       for el in connection_order]

    for spec in model.projections:
        matches = [(i, el) for i, el in enumerate(connection_list)
                   if el[0] == spec.matchname]
        if len(matches) == 0:
            raise Exception("Could not order projection %r" % spec)
        elif len(matches) == 1:
            (i, (k, v)) = matches[0]
            spec.sort_precedence = i
            continue

        property_keys = [pdict.keys() for (_, (_, pdict)) in matches]
        if not all(len(pkeys)==1 for pkeys in property_keys):
            raise Exception("Please specify only a single property to sort on")
        if not all(pkey[0]==property_keys[0][0] for pkey in property_keys):
            raise Exception("Please specify only a single property to sort on")

        key = property_keys[0][0]
        spec_property_value = spec.src.properties[key]
        match = [ind for (ind, (_, pdict)) in matches if pdict[key] == spec_property_value]
        if len(match) != 1:
            raise Exception("Could not order projection %r by property %r" % (spec, key))
        spec.sort_precedence = match[0]



class Specification(object):
    """
    Specifications are templates for sheet or projection objects which
    may be resolved to the corresponding simulation object once
    instantiated.

    All specifications have the following attribute:

    :'parameters': Keyword argument dictionary specifying which
    parameters should be passed to the sheet or projection object.
    """

    def update(self, **params):
        """
        Convenience method to easy update specification parameters.
        """
        self.parameters.update(params)

    @property
    def modified_parameters(self):
        "Dictionary of modified specification parameters"
        return {k:v for k, v in self.parameters.items()
                if self.default_parameters[k] != v}


    def resolve(self):
        """
        Returns the object in topo.sim corresponding to the string
        name of this object, typically a Sheet or a Projection.

        The appropriate object must be instantiated in topo.sim first.
        """
        from topo import sim # pyflakes:ignore (needed for eval)
        return eval('sim.'+str(self))


    def __lt__(self, other):
        return self.sort_precedence < other.sort_precedence

    def __eq__(self, other):
        return self.sort_precedence == other.sort_precedence

    def __init__(self, object_type):
        self.parameters = {}
        self.sort_precedence = 0
        for param_name, default_value in object_type.params().items():
            self.parameters[param_name]=default_value.default
        self.default_parameters = dict(**self.parameters)

    def summary(self, printed=True):
        """
        Generate a succinct summary of the Specification object. If
        printed is set to True, the summary is printed, otherwise it
        is returned as a string.
        """
        raise NotImplementedError


class SheetSpec(Specification):
    """
    SheetSpec acts as a template for sheet objects.
    """

    name_ordering = ['eye','level', 'cone', 'polarity',
                     'SF','opponent','surround']

    @property
    def level(self):
        return self.properties['level']


    def __init__(self, sheet_type, properties):
        """
        Initialize a SheetSpec object of a certain Sheet type with the
        given properties.

       :'sheet_type': Subclass of topo.base.sheet.Sheet.
       :'properties': Dictionary specifying the properties of the
       sheet. There must be a value given for the key 'level'.
        """
        super(SheetSpec,self).__init__(sheet_type)

        if 'level' not in properties:
            raise Exception("SheetSpec always requires 'level' property.")


        properties = [(k, properties[k]) for k in self.name_ordering
                      if k in properties]

        self.sheet_type = sheet_type
        self.properties = OrderedDict(properties)


    def __call__(self):
        """
        Instantiate the sheet and register it in topo.sim.
        """
        properties = dict(self.parameters['properties'], **self.properties)
        topo.sim[str(self)]=self.sheet_type(**dict(self.parameters,
                                                   properties=properties))

    def __str__(self):
        """
        Returns a string representation of the SheetSpec from the
        properties values.
        """
        name=''
        for prop in self.properties.itervalues():
            name+=str(prop)

        return name

    def summary(self, printed=True):
        summary = "%s : %s" % (self, self.sheet_type.name)
        if printed: print summary
        else:       return summary

    def __repr__(self):
        type_name = self.sheet_type.__name__
        properties_repr = ', '.join("%r:%r" % (k,v) for (k,v)
                                    in self.properties.items())
        return "SheetSpec(%s, {%s})" % (type_name, properties_repr)



class ProjectionSpec(Specification):
    """
    ProjectionSpec acts as a template for projection objects.
    """

    def __init__(self, projection_type, src, dest):
        """
        Initialize a ProjectionSpec object of a certain Projection
        type with the given src and dest SheetSpecs.

       :'projection_type': Subclass of topo.base.projection.Projection
       :'src': SheetSpec of the source sheet
       :'dest': SheetSpec of the destination sheet
        """
        super(ProjectionSpec, self).__init__(projection_type)

        self.projection_type = projection_type
        self.src = src
        self.dest = dest

        # These parameters are directly passed into topo.sim.connect()!
        ignored_keys = ['src', 'dest']
        self.parameters = dict((k,v) for (k,v) in self.parameters.items()
                               if k not in ignored_keys)

    def __call__(self):
        """
        Instantiate the projection and register it in topo.sim.
        """
        topo.sim.connect(str(self.src),str(self.dest),
                         self.projection_type,
                         **self.parameters)

    def __str__(self):
        return str(self.dest)+'.'+self.parameters['name']


    def summary(self, printed=True):
        summary = "%s [%s -> %s] : %s" % (self, self.src, self.dest,
                                          self.projection_type.name)
        if printed: print summary
        else:       return summary

    def __repr__(self):
        type_name = self.projection_type.__name__
        return "ProjectionSpec(%s, %r, %r)" % (type_name, self.src, self.dest)



class ClassDecorator(object):
    """
    Decorator class which can be instantiated to create a decorator
    object to annotate method with a certain type.

    After decorating several methods or functions, the dictionary of
    the decorated callables may be accessed via the labels
    attribute. Object types are accessible via the types attribute.
    """

    # Priority is needed to ensure that a decorator method in a
    # subclass takes priority over a decorated method (of the same
    # name) in the superclass
    priority = 0

    def __init__(self, name, object_type):
        self.name = name
        self.labels = {}
        self.types = {}
        self.type = object_type

        # Enable IPython tab completion in the settings method
        kwarg_string = ", ".join("%s=%s" % (name, type(p.default))
                                 for (name, p) in object_type.params().items())
        self.params.__func__.__doc__ =  'params(%s)' % kwarg_string


    def params(self, **kwargs):
        """
        A convenient way of generating parameter dictionaries with
        tab-completion in IPython.
        """
        return kwargs


    def __call__(self, f):
        label = f.__name__
        @wraps(f)
        def inner(*args, **kwargs):
            return f(*args, **kwargs)

        self.types[label] = (ClassDecorator.priority, self.type)
        self.labels[label] = (ClassDecorator.priority, inner)
        ClassDecorator.priority += 1
        return inner


    def __repr__(self):
        return "ClassDecorator(%s, %s)" % (self.name, self.type.name)



class MatchConditions(object):
    """
    Decorator class for matchconditions.
    """
    def __init__(self):
        self._levels = {}


    def compute_conditions(self, level, model, properties):
        """
        Collect the matchcondition dictionary for a particular level
        given a certain Model instance and sheet properties.
        """
        if level not in self:
            raise Exception("No level %r defined" % level)
        return dict((k, fn(model, properties))
                     for (k, fn) in self._levels[level].items())


    def __call__(self, level, method_name):
        def decorator(f):
            @wraps(f)
            def inner(self, *args, **kwargs):
                return f(self, *args, **kwargs)

            if level not in self._levels:
                self._levels[level] = {method_name:inner}
            else:
                self._levels[level][method_name] = inner
            return inner
        return decorator

    def __repr__(self):
        return "MatchConditions()"

    def __contains__(self, key):
        return key in self._levels



class Model(param.Parameterized):
    """
    The available setup options are:

        :'training_patterns': fills the training_patterns AttrTree
        with pattern generator instances. The path is the name of the
        input sheet. Usually calls PatternCoordinator to do this.
        :'setup_sheets': determines the number of sheets, their types
        and names sets sheet parameters according to the registered
        methods in level sets sheet matchconditions according to the
        registered methods in matchconditions
        :'projections': determines which connections should be present
        between the sheets according to the matchconditions of
        SheetSpec objects, using connect to specify the
        connection type and sets their parameters according to the
        registered methods in connect


    The available instantiate options are:

        :'sheets': instantiates all sheets and registers them in
        topo.sim
        :'projections': instantiates all projections and registers
        them in topo.sim
    """
    __abstract = True

    matchconditions = MatchConditions()

    sheet_decorators = set()
    projection_decorators = set()

    @classmethod
    def register_decorator(cls, object_type):
        name = object_type.name
        decorator = ClassDecorator(name, object_type)
        setattr(cls, name,  decorator)

        if issubclass(object_type, topo.sheet.Sheet):
            cls.sheet_decorators.add(decorator)
        if issubclass(object_type, topo.projection.Projection):
            cls.projection_decorators.add(decorator)

    @classmethod
    def _collect(cls, decorators, name):
        """
        Given a list of ClassDecorators (e.g self.sheet_decorators or
        self.projection_decorators), collate the named attribute
        (i.e. 'types' or 'labels') across the decorators according to
        priority.
        """
        flattened = [el for d in decorators for el in getattr(d, name).items()]
        return dict((k,v) for (k, (_, v)) in sorted(flattened, key=lambda x: x[1][0]))


    @property
    def sheet_labels(self):
        "The mapping of level method to corresponding label"
        return self._collect(self.sheet_decorators, 'labels')

    @property
    def sheet_types(self):
        "The mapping of level label to sheet type"
        return self._collect(self.sheet_decorators, 'types')

    @property
    def projection_labels(self):
        "The mapping of projection method to corresponding label"
        return self._collect(self.projection_decorators, 'labels')

    @property
    def projection_types(self):
        "The mapping of projection label to projection type"
        return self._collect(self.projection_decorators, 'types')

    @property
    def modified_parameters(self):
        "Dictionary of modified model parameters"
        return {k:v for k,v in self.get_param_values(onlychanged=True)}


    def __init__(self, setup_options=True, register=True, time_dependent=True, **params):
        numbergen.TimeAware.time_dependent = time_dependent
        if register:
            self._register_global_params(params)
        super(Model,self).__init__(**params)

        self._sheet_types = {}
        self._projection_types = {}

        self.attrs = AttrTree()
        self.training_patterns = AttrTree()
        self.sheets = AttrTree()
        self.projections = AttrTree()

        self.setup(setup_options)


    def _register_global_params(self, params):
        """
        Register the parameters of this object as global parameters
        available for users to set from the command line.  Values
        supplied as global parameters will override those of the given
        dictionary of params.
        """

        for name,obj in self.params().items():
            global_params.add(**{name:obj})

        for name,val in params.items():
            global_params.params(name).default=val

        params.update(global_params.get_param_values())
        params["name"]=self.name


    #==============================================#
    # Public methods to be implemented by modelers #
    #==============================================#

    def setup_attributes(self, attrs):
        """
        Method to precompute any useful attributes from the class
        parameters. For instance, if there is a ``num_lags``
        parameter, this method could compute the actual projection
        delays and store it as attrs.lags. The return value is the
        updated attrs AttrTree.

        In addition, this method can be used to configure class
        attributes of the model components.
        """
        return attrs


    def setup_training_patterns(self, **overrides):
        """
        Returns a dictionary of PatternGenerators to be added to
        self.training_patterns, with the target sheet name keys and
        pattern generator values.

        The overrides keywords can be used by a subclass to
        parameterize the training patterns e.g. override the default
        parameters of a PatternCoordinator object.
        """
        raise NotImplementedError


    def setup_sheets(self):
        """
        Returns a dictionary of properties or equivalent Lancet.Args
        object. Each outer key must be the level name and the values
        are lists of property dictionaries for the sheets at that
        level (or equivalent Lancet Args object). For instance, two
        LGN sheets at the 'LGN' level could be defined by either:

        {'LGN':[{'polarity':'ON'}, {'polarity':'OFF'}]}
        OR
        {'LGN':lancet.List('polarity', ['ON', 'OFF'])}

        The specified properties are used to initialize the sheets
        AttrTree with SheetSpec objects.
        """
        raise NotImplementedError


    def setup_analysis(self):
        """
        Set up appropriate defaults for analysis functions in
        topo.analysis.featureresponses.
        """
        pass


    #====================================================#
    # Remaining methods should not need to be overridden #
    #====================================================#

    def setup(self,setup_options):
        """
        This method can be used to setup certain parts of the
        submodel.  If setup_options=True, all setup methods are
        called.  setup_options can also be a list, whereas all list
        items of available_setup_options are accepted.

        Available setup options are:
        'training_patterns','sheets','projections' and 'analysis'.

        Please consult the docstring of the Model class for more
        information about each setup option.
        """
        available_setup_options = ['attributes',
                                   'training_patterns',
                                   'sheets',
                                   'projections',
                                   'analysis']

        if setup_options==True:
            setup_options = available_setup_options

        if 'attributes' in setup_options:
            self.attrs = self.setup_attributes(self.attrs)

        if 'training_patterns' in setup_options:
            training_patterns = self.setup_training_patterns()
            for name, training_pattern in training_patterns.items():
                self.training_patterns.set_path(name, training_pattern)
        if 'sheets' in setup_options:
            sheet_properties = self.setup_sheets()

            enumeration = enumerate(sheet_properties.items())
            for (ordering, (level, property_list)) in enumeration:
                sheet_type = self.sheet_types[level]

                if isinstance(property_list, lancet.Identity):
                    property_list = [{}]
                elif isinstance(property_list, lancet.Args):
                    property_list = property_list.specs
                # If an empty list or Args()
                elif not property_list:
                    continue

                for properties in property_list:
                    spec_properties = dict(level=level, **properties)
                    sheet_spec = SheetSpec(sheet_type, spec_properties)
                    sheet_spec.sort_precedence = ordering
                    self.sheets.set_path(str(sheet_spec), sheet_spec)

            self._update_sheet_spec_parameters()
        if 'projections' in setup_options:
            self._compute_projection_specs()
        if 'analysis' in setup_options:
            self._setup_analysis()


    def _update_sheet_spec_parameters(self):
        for sheet_spec in self.sheets.path_items.values():
            param_method = self.sheet_labels.get(sheet_spec.level, None)
            if not param_method:
                raise Exception("Parameters for sheet level %r not specified" % sheet_spec.level)

            updated_params = param_method(self,sheet_spec.properties)
            sheet_spec.update(**updated_params)


    def _matchcondition_holds(self, matchconditions, src_sheet):
        """
        Given a dictionary of properties to match and a target sheet
        spec, return True if the matchcondition holds else False.
        """
        matches=True
        if matchconditions is None:
            return False
        for incoming_key, incoming_value in matchconditions.items():
            if incoming_key in src_sheet.properties and \
                    str(src_sheet.properties[incoming_key]) not in str(incoming_value):
                matches=False
                break

        return matches

    def _compute_projection_specs(self):
        """
        Loop through all possible combinations of SheetSpec objects in
        self.sheets If the src_sheet fulfills all criteria specified
        in dest_sheet.matchconditions, create a new ProjectionSpec
        object and add this item to self.projections.
        """
        sheetspec_product = itertools.product(self.sheets.path_items.values(),
                                              self.sheets.path_items.values())
        for src_sheet, dest_sheet in sheetspec_product:

            has_matchcondition = (dest_sheet.level in self.matchconditions)
            conditions = (self.matchconditions.compute_conditions(
                          dest_sheet.level, self,dest_sheet.properties)
                          if has_matchcondition else {})

            for matchname, matchconditions in conditions.items():

                if self._matchcondition_holds(matchconditions, src_sheet):
                    proj = ProjectionSpec(self.projection_types[matchname],
                                          src_sheet, dest_sheet)

                    paramsets = self.projection_labels[matchname](self, src_sheet.properties,
                                                                  dest_sheet.properties)
                    paramsets = [paramsets] if isinstance(paramsets, dict) else paramsets
                    for paramset in paramsets:
                        proj = ProjectionSpec(self.projection_types[matchname],
                                              src_sheet, dest_sheet)
                        proj.update(**paramset)
                        # Only used when time_dependent=False
                        # (which is to be deprecated)
                        proj.matchname = matchname

                        path = (str(dest_sheet), paramset['name'])
                        self.projections.set_path(path, proj)


    def __call__(self,instantiate_options=True, verbose=False):
        """
        Instantiates all sheets or projections in self.sheets or
        self.projections and registers them in the topo.sim instance.

        If instantiate_options=True, all items are initialised
        instantiate_options can also be a list, whereas all list items
        of available_instantiate_options are accepted.

        Available instantiation options are: 'sheets' and
        'projections'.

        Please consult the docstring of the Model class for more
        information about each instantiation option.
        """
        msglevel = self.message if verbose else self.debug
        available_instantiate_options = ['sheets','projections']
        if instantiate_options==True:
            instantiate_options=available_instantiate_options

        if 'sheets' in instantiate_options:
            for sheet_spec in self.sheets.path_items.itervalues():
                msglevel('Level ' + sheet_spec.level + ': Sheet ' + str(sheet_spec))
                sheet_spec()

        if 'projections' in instantiate_options:
            for proj in sorted(self.projections):
                msglevel('Match: ' + proj.matchname + ': Connection ' + str(proj.src) + \
                             '->' + str(proj.dest) + ' ' + proj.parameters['name'])
                proj()

    def summary(self, printed=True):

        heading_line = '=' * len(self.name)
        summary = [heading_line, self.name, heading_line, '']

        for sheet_spec in sorted(self.sheets):
            summary.append(sheet_spec.summary(printed=False))
            projections = [proj for proj in self.projections
                           if str(proj).startswith(str(sheet_spec))]
            for projection_spec in sorted(projections, key=lambda p: str(p)):
                summary.append("   " + projection_spec.summary(printed=False))
            summary.append('')

        if printed: print "\n".join(summary)
        else:       return "\n".join(summary)


    def __str__(self):
        return self.name


    def _repr_pretty_(self, p, cycle):
        p.text(self.summary(printed=False))


    def modifications(self, components=['model', 'sheets', 'projections']):
        """
        Display the names of all modified parameters for the specified
        set of components.

        By default all modified parameters are listed - first with the
        model parameters, then the sheet parameters and lastly the
        projection parameters.
        """
        mapping = {'model': [self],
                   'sheets':self.sheets,
                   'projections':self.projections}

        lines = []
        for component in components:
            heading = "=" * len(component)
            lines.extend([heading, component.capitalize(), heading, ''])
            specs = mapping[component]
            padding = max(len(str(spec)) for spec in specs)
            for spec in sorted(specs):
                modified = [str(el) for el in sorted(spec.modified_parameters)]
                lines.append("%s : [%s]" % (str(spec).ljust(padding), ", ".join(modified)))
            lines.append('')
        print "\n".join(lines)


# Register the sheets and projections available in Topographica
from topo.sheet import optimized as sheetopt
from topo.projection import optimized as projopt
from topo import projection

sheet_classes = [c for c in topo.sheet.__dict__.values() if
                 (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

sheet_classes_opt = [c for c in sheetopt.__dict__.values() if
                     (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

projection_classes = [c for c in projection.__dict__.values() if
                      (isinstance(c, type) and issubclass(c, projection.Projection))]

projection_classes_opt = [c for c in projopt.__dict__.values() if
                          (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

for obj_class in (sheet_classes + sheet_classes_opt
                  + projection_classes + projection_classes_opt):
    with param.logging_level('CRITICAL'):
        # Do not create a decorator if declared as abstract
        if not hasattr(obj_class, "_%s__abstract" % obj_class.name):
            Model.register_decorator(obj_class)
