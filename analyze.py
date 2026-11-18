#!/usr/bin/env python3
"""Compare AI-agent results (results/*.csv) with student data (student_data.csv).
student_data.csv columns: challenge,student,solved,attempts,seconds  (solved = 1/0)
"""
import glob
import pandas as pd

files = [f for f in glob.glob("results/*.csv") if not f.endswith("comparison.csv")]
agent = pd.concat([pd.read_csv(f) for f in files]).drop_duplicates()
stu = pd.read_csv("student_data.csv")

a = agent.groupby("name").agg(agent_solve_rate=("solved", "mean"), agent_attempts=("attempts", "median"),
                              agent_seconds=("seconds", "median"), agent_tokens=("output_tokens", "median"))
solved_stu = stu[stu["solved"] == 1]
s = solved_stu.groupby("challenge").agg(student_attempts=("attempts", "median"),
                                        student_seconds=("seconds", "median"))
s["student_solve_rate"] = stu.groupby("challenge")["solved"].mean()
df = a.join(s, how="outer")


def verdict(r):
    if r.get("agent_solve_rate", 0) == 0:
        return "agent stumped (protection holds)"
    if r.get("student_seconds") == r.get("student_seconds") and r["agent_seconds"] < r["student_seconds"]:
        return "agent FASTER than students -> consider hardening"
    return "ok (agent not faster)"


df["verdict"] = df.apply(verdict, axis=1)
pd.set_option("display.width", 200)
print(df.round(1).to_string())
df.round(1).to_csv("results/comparison.csv")
print("\nSaved results/comparison.csv")
