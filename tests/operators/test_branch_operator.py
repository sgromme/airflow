#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import datetime

import pytest

from airflow.models.dag import DAG
from airflow.models.dagrun import DagRun
from airflow.models.taskinstance import TaskInstance as TI
from airflow.operators.branch import BaseBranchOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils import timezone
from airflow.utils.session import create_session
from airflow.utils.state import State
from airflow.utils.task_group import TaskGroup
from airflow.utils.types import DagRunType

pytestmark = pytest.mark.db_test

DEFAULT_DATE = timezone.datetime(2016, 1, 1)
INTERVAL = datetime.timedelta(hours=12)


class ChooseBranchOne(BaseBranchOperator):
    def choose_branch(self, context):
        return "branch_1"


class ChooseBranchOneTwo(BaseBranchOperator):
    def choose_branch(self, context):
        return ["branch_1", "branch_2"]


class ChooseBranchThree(BaseBranchOperator):
    def choose_branch(self, context):
        return ["branch_3"]


class TestBranchOperator:
    @classmethod
    def setup_class(cls):
        with create_session() as session:
            session.query(DagRun).delete()
            session.query(TI).delete()

    def setup_method(self):
        self.dag = DAG(
            "branch_operator_test",
            default_args={"owner": "airflow", "start_date": DEFAULT_DATE},
            schedule=INTERVAL,
        )

        self.branch_1 = EmptyOperator(task_id="branch_1", dag=self.dag)
        self.branch_2 = EmptyOperator(task_id="branch_2", dag=self.dag)
        self.branch_3 = None
        self.branch_op = None

    def teardown_method(self):
        with create_session() as session:
            session.query(DagRun).delete()
            session.query(TI).delete()

    def test_without_dag_run(self):
        """This checks the defensive against non existent tasks in a dag run"""
        self.branch_op = ChooseBranchOne(task_id="make_choice", dag=self.dag)
        self.branch_1.set_upstream(self.branch_op)
        self.branch_2.set_upstream(self.branch_op)
        self.dag.clear()

        self.branch_op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)

        with create_session() as session:
            tis = session.query(TI).filter(TI.dag_id == self.dag.dag_id, TI.execution_date == DEFAULT_DATE)

            for ti in tis:
                if ti.task_id == "make_choice":
                    assert ti.state == State.SUCCESS
                elif ti.task_id == "branch_1":
                    # should exist with state None
                    assert ti.state == State.NONE
                elif ti.task_id == "branch_2":
                    assert ti.state == State.SKIPPED
                else:
                    raise Exception

    def test_branch_list_without_dag_run(self):
        """This checks if the BranchOperator supports branching off to a list of tasks."""
        self.branch_op = ChooseBranchOneTwo(task_id="make_choice", dag=self.dag)
        self.branch_1.set_upstream(self.branch_op)
        self.branch_2.set_upstream(self.branch_op)
        self.branch_3 = EmptyOperator(task_id="branch_3", dag=self.dag)
        self.branch_3.set_upstream(self.branch_op)
        self.dag.clear()

        self.branch_op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)

        with create_session() as session:
            tis = session.query(TI).filter(TI.dag_id == self.dag.dag_id, TI.execution_date == DEFAULT_DATE)

            expected = {
                "make_choice": State.SUCCESS,
                "branch_1": State.NONE,
                "branch_2": State.NONE,
                "branch_3": State.SKIPPED,
            }

            for ti in tis:
                if ti.task_id in expected:
                    assert ti.state == expected[ti.task_id]
                else:
                    raise Exception

    def test_with_dag_run(self):
        self.branch_op = ChooseBranchOne(task_id="make_choice", dag=self.dag)
        self.branch_1.set_upstream(self.branch_op)
        self.branch_2.set_upstream(self.branch_op)
        self.dag.clear()

        dagrun = self.dag.create_dagrun(
            run_type=DagRunType.MANUAL,
            start_date=timezone.utcnow(),
            execution_date=DEFAULT_DATE,
            state=State.RUNNING,
            data_interval=(DEFAULT_DATE, DEFAULT_DATE),
        )

        self.branch_op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)

        tis = dagrun.get_task_instances()
        for ti in tis:
            if ti.task_id == "make_choice":
                assert ti.state == State.SUCCESS
            elif ti.task_id == "branch_1":
                assert ti.state == State.NONE
            elif ti.task_id == "branch_2":
                assert ti.state == State.SKIPPED
            else:
                raise Exception

    def test_with_skip_in_branch_downstream_dependencies(self):
        self.branch_op = ChooseBranchOne(task_id="make_choice", dag=self.dag)
        self.branch_op >> self.branch_1 >> self.branch_2
        self.branch_op >> self.branch_2
        self.dag.clear()

        dagrun = self.dag.create_dagrun(
            run_type=DagRunType.MANUAL,
            start_date=timezone.utcnow(),
            execution_date=DEFAULT_DATE,
            state=State.RUNNING,
            data_interval=(DEFAULT_DATE, DEFAULT_DATE),
        )

        self.branch_op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)

        tis = dagrun.get_task_instances()
        for ti in tis:
            if ti.task_id == "make_choice":
                assert ti.state == State.SUCCESS
            elif ti.task_id in ("branch_1", "branch_2"):
                assert ti.state == State.NONE
            else:
                raise Exception

    def test_xcom_push(self):
        self.branch_op = ChooseBranchOne(task_id="make_choice", dag=self.dag)

        self.branch_1.set_upstream(self.branch_op)
        self.branch_2.set_upstream(self.branch_op)
        self.dag.clear()

        dr = self.dag.create_dagrun(
            run_type=DagRunType.MANUAL,
            start_date=timezone.utcnow(),
            execution_date=DEFAULT_DATE,
            state=State.RUNNING,
            data_interval=(DEFAULT_DATE, DEFAULT_DATE),
        )

        self.branch_op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)

        tis = dr.get_task_instances()
        for ti in tis:
            if ti.task_id == "make_choice":
                assert ti.xcom_pull(task_ids="make_choice") == "branch_1"

    def test_with_dag_run_task_groups(self):
        self.branch_op = ChooseBranchThree(task_id="make_choice", dag=self.dag)
        self.branch_3 = TaskGroup("branch_3", dag=self.dag)
        _ = EmptyOperator(task_id="task_1", dag=self.dag, task_group=self.branch_3)
        _ = EmptyOperator(task_id="task_2", dag=self.dag, task_group=self.branch_3)

        self.branch_1.set_upstream(self.branch_op)
        self.branch_2.set_upstream(self.branch_op)
        self.branch_3.set_upstream(self.branch_op)

        self.dag.clear()

        dagrun = self.dag.create_dagrun(
            run_type=DagRunType.MANUAL,
            start_date=timezone.utcnow(),
            execution_date=DEFAULT_DATE,
            state=State.RUNNING,
            data_interval=(DEFAULT_DATE, DEFAULT_DATE),
        )

        self.branch_op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)

        tis = dagrun.get_task_instances()
        for ti in tis:
            if ti.task_id == "make_choice":
                assert ti.state == State.SUCCESS
            elif ti.task_id == "branch_1":
                assert ti.state == State.SKIPPED
            elif ti.task_id == "branch_2":
                assert ti.state == State.SKIPPED
            elif ti.task_id == "branch_3.task_1":
                assert ti.state == State.NONE
            elif ti.task_id == "branch_3.task_2":
                assert ti.state == State.NONE
            else:
                raise Exception
