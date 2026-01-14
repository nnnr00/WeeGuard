from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True)
    points = Column(Integer, default=0)
    last_signin = Column(DateTime)
    paid_wechat = Column(Integer, default=0)
    paid_alipay = Column(Integer, default=0)

class Reward(Base):
    __tablename__ = 'rewards'
    id = Column(String, primary_key=True)
    title = Column(String)
    description = Column(String)
    cost = Column(Integer)

class RewardCode(Base):
    __tablename__ = 'reward_codes'
    id = Column(Integer, primary_key=True)
    reward_id = Column(String, ForeignKey('rewards.id'))
    code = Column(String)
    is_used = Column(Integer, default=0)
    used_by = Column(Integer, nullable=True)
