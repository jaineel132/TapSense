def get_handler(handlers, command):
    """
    Returns the handler registered for the given gesture command.
    """
    for (prefix, handler) in handlers.items():

        if command.lower().startswith(prefix):
            return handler

    # Nothing matches
    raise LookupError("No handler was found for '%s'" % command)
