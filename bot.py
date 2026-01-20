def init_database():
    """初始化数据库表（完全保护现有数据）"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        logger.info("🔍 开始检查数据库结构...")
        
        # ==================== 检查并创建 users 表 ====================
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'users'
            )
        """)
        users_exists = cur.fetchone()[0]
        
        if users_exists:
            logger.info("✅ users 表已存在，检查列...")
            
            # 检查并添加缺少的列（不影响现有数据）
            columns_to_add = [
                ("username", "VARCHAR(255)"),
                ("first_name", "VARCHAR(255)"),
                ("points", "INTEGER DEFAULT 0"),
                ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
                ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            ]
            
            for col_name, col_type in columns_to_add:
                try:
                    cur.execute(f"""
                        ALTER TABLE users 
                        ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                    """)
                    conn.commit()
                except Exception as e:
                    logger.warning(f"列 {col_name} 可能已存在: {e}")
                    conn.rollback()
            
            logger.info("✅ users 表结构更新完成（所有数据保留）")
        else:
            # 首次创建表
            cur.execute('''
                CREATE TABLE users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    points INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("✅ users 表创建成功")
        
        # ==================== 检查并创建 ad_views 表 ====================
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'ad_views'
            )
        """)
        ad_views_exists = cur.fetchone()[0]
        
        if ad_views_exists:
            logger.info("✅ ad_views 表已存在，检查列...")
            
            columns_to_add = [
                ("user_id", "BIGINT"),
                ("view_date", "DATE"),
                ("view_count", "INTEGER DEFAULT 0"),
                ("points_earned", "INTEGER DEFAULT 0"),
                ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            ]
            
            for col_name, col_type in columns_to_add:
                try:
                    cur.execute(f"""
                        ALTER TABLE ad_views 
                        ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                    """)
                    conn.commit()
                except Exception as e:
                    logger.warning(f"列 {col_name} 可能已存在: {e}")
                    conn.rollback()
            
            logger.info("✅ ad_views 表结构更新完成（所有数据保留）")
        else:
            cur.execute('''
                CREATE TABLE ad_views (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    view_date DATE NOT NULL,
                    view_count INTEGER DEFAULT 0,
                    points_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("✅ ad_views 表创建成功")
        
        # 添加唯一约束（如果不存在）
        try:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_constraint 
                    WHERE conname = 'ad_views_user_date_unique'
                )
            """)
            constraint_exists = cur.fetchone()[0]
            
            if not constraint_exists:
                cur.execute('''
                    ALTER TABLE ad_views 
                    ADD CONSTRAINT ad_views_user_date_unique 
                    UNIQUE (user_id, view_date)
                ''')
                conn.commit()
                logger.info("✅ ad_views 唯一约束添加成功")
        except Exception as e:
            logger.warning(f"约束可能已存在: {e}")
            conn.rollback()
        
        # ==================== 检查并创建 verifications 表 ====================
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'verifications'
            )
        """)
        verifications_exists = cur.fetchone()[0]
        
        if verifications_exists:
            logger.info("✅ verifications 表已存在，检查列...")
            
            columns_to_add = [
                ("user_id", "BIGINT"),
                ("verification_code", "VARCHAR(100)"),
                ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
                ("is_used", "BOOLEAN DEFAULT FALSE")
            ]
            
            for col_name, col_type in columns_to_add:
                try:
                    cur.execute(f"""
                        ALTER TABLE verifications 
                        ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                    """)
                    conn.commit()
                except Exception as e:
                    logger.warning(f"列 {col_name} 可能已存在: {e}")
                    conn.rollback()
            
            logger.info("✅ verifications 表结构更新完成（所有数据保留）")
        else:
            cur.execute('''
                CREATE TABLE verifications (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    verification_code VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_used BOOLEAN DEFAULT FALSE
                )
            ''')
            conn.commit()
            logger.info("✅ verifications 表创建成功")
        
        # ==================== 创建索引（提高性能）====================
        indexes = [
            ('idx_ad_views_user_date', 'ad_views', '(user_id, view_date)'),
            ('idx_verifications_user', 'verifications', '(user_id)'),
            ('idx_verifications_code', 'verifications', '(verification_code)')
        ]
        
        for idx_name, table_name, columns in indexes:
            try:
                cur.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} {columns}')
                conn.commit()
            except Exception as e:
                logger.warning(f"索引 {idx_name} 跳过: {e}")
                conn.rollback()
        
        logger.info("✅ 索引检查完成")
        
        # ==================== 统计现有数据 ====================
        try:
            cur.execute('SELECT COUNT(*) FROM users')
            user_count = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM ad_views')
            ad_views_count = cur.fetchone()[0]
            
            cur.execute('SELECT SUM(points) FROM users')
            total_points = cur.fetchone()[0] or 0
            
            logger.info(f"""
╔══════════════════════════════════════╗
║    数据库初始化完成（数据安全）      ║
╠══════════════════════════════════════╣
║  👥 用户数量: {user_count:>20}   ║
║  📊 观看记录: {ad_views_count:>20}   ║
║  💰 总积分数: {total_points:>20}   ║
╚══════════════════════════════════════╝
            """)
        except Exception as e:
            logger.warning(f"统计信息获取失败: {e}")
        
        logger.info("🎉 数据库就绪，所有现有数据已完整保留！")
        
    except Exception as e:
        logger.error(f"❌ 数据库初始化错误: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
