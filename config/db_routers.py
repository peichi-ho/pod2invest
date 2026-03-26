class EtfRouter:
    route_app_labels = {"etf"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "etfdb"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "etfdb"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        db_list = {"default", "etfdb"}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "etfdb"
        return None
    
class SummariesRouter:
    route_app_labels = {"summaries"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "summariesdb"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "summariesdb"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        db_list = {"default", "summariesdb"}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "summariesdb"
        return None
