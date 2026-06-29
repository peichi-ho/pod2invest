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
        if db == "etfdb":
            return False
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
        db_list = {"default", "summariesdb", "podcasts"}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "summariesdb"
        if db == "summariesdb":
            return False
        return None

class PodcastsRouter:
    route_app_labels = {"podcasts"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "podcasts"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "podcasts"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        db_list = {"default", "podcasts", "summariesdb"}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "podcasts"
        if db == "podcasts":
            return False
        return None

class KnowledgeGraphRouter:
    route_app_labels = {"knowledge_graph"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "knowledge_graphdb"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "knowledge_graphdb"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        db_list = {"default", "knowledge_graphdb"}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "knowledge_graphdb"
        if db == "knowledge_graphdb":
            return False
        return None


class AccountsRouter:
    route_app_labels = {"accounts"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "accountsdb"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "accountsdb"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._state.db == "accountsdb" or obj2._state.db == "accountsdb":
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "accountsdb"
        if db == "accountsdb":
            return False
        return None


class AiAssistantRouter:
    route_app_labels = {"ai_assistant"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "ai_assistant_db"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "ai_assistant_db"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        db_list = {"ai_assistant_db"}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "ai_assistant_db"
        if db == "ai_assistant_db":
            return False
        return None
