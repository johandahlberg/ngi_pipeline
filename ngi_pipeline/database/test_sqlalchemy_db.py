import os
import random
import tempfile
import unittest

from ngi_pipeline.database import sqlalchemy_db as sql_db
from ngi_pipeline.tests import generate_test_data as gtd



class TestSqlAlchemyDB(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp()
        cls.database_path = os.path.join(cls.tmp_dir, "temporary_database")
        cls.session = sql_db.get_db_session(database_path=cls.database_path)
        cls.project_name = "Y.Mom_14_01"
        cls.project_id = "P123"
        cls.sample_id = "{}_456".format(cls.project_id)
        cls.libprep_id = "A"
        cls.seqrun_id = gtd.generate_run_id()
        cls.engine = "piper_ngi"

    def setUp(self):
        self.process_id = random.randint(2,65536)

    def test_add_seqrun_analysis(self):
        seqrun_analysis = sql_db.SeqrunAnalysis(project_id=self.project_id,
                                                sample_id=self.sample_id,
                                                libprep_id=self.libprep_id,
                                                seqrun_id=self.seqrun_id,
                                                lane_num=random.randint(1,8),
                                                engine=self.engine,
                                                process_id=self.process_id)
        self.session.add(seqrun_analysis)
        self.session.commit()

        # There can be only one()
        query = self.session.query(sql_db.SeqrunAnalysis).filter_by(
                                        process_id=self.process_id).one()
        self.assertEqual(query, seqrun_analysis)