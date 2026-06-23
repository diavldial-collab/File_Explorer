# ====================================================================================
# 1. 라이브러리 임포트 (Library Import)
# ====================================================================================
    # 이 섹션에서는 프로그램 실행에 필요한 모든 외부 라이브러리를 임포트합니다.
    # - PySide6: GUI 구성 및 이벤트 처리
    # - 파일/시스템: 파일 탐색, 경로 관리, 압축 처리
    # - 데이터베이스: 파일 인덱싱 및 빠른 검색을 위한 SQLite
    # - 유틸리티: 날짜/시간 처리, 패턴 매칭, 해시 생성

# 시스템 및 파일 관리 라이브러리 ------------------------------------------------
import sys
import os
import json
import shutil
import subprocess
import platform
import zipfile
from pathlib import Path

# PySide6 위젯 라이브러리 ---------------------------------------------------------
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeView, QListWidget, QSplitter, QLineEdit, QPushButton,
    QMenu, QInputDialog, QMessageBox, QLabel,
    QStatusBar, QTextEdit, QDialog, QDialogButtonBox,
    QProgressDialog,
    QAbstractItemView, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QSpinBox, QDateEdit, QComboBox, QGroupBox, QRadioButton,
    QButtonGroup, QTabBar
)

# PySide6 그래픽 라이브러리 -------------------------------------------------------
from PySide6.QtGui import (
    QKeySequence, QIcon, QPainter, QPainterPath, QPixmap, QColor, QPen, QTransform, QPalette, QShortcut,
    QAction, QActionGroup, QFont,
)

# PySide6 코어 라이브러리 ---------------------------------------------------------
from PySide6.QtCore import Qt, QFileInfo, QSize, QEvent, QThread, Signal, QDate, QSortFilterProxyModel, QRectF, QTimer, QPropertyAnimation, QPoint, Property
from PySide6.QtWidgets import QFileSystemModel, QFileIconProvider

# 외부 라이브러리 ---------------------------------------------------------------
from send2trash import send2trash                                      # 휴지통 삭제 (pip install send2trash)
import fnmatch                                                          # 파일명 패턴 매칭
import time                                                             # 시간 처리
from datetime import datetime, timedelta                                # 날짜/시간 연산
import sqlite3                                                          # 파일 인덱싱 데이터베이스
import hashlib                                                          # 해시 생성


# ====================================================================================
# 2. 워커 스레드 클래스 (Worker Thread Classes)
# ====================================================================================
    # 백그라운드 작업을 처리하는 QThread 기반 워커 클래스들입니다.
    # - IndexWorker: 파일 시스템 인덱싱
    # - SearchWorker: 파일 검색

class IndexWorker(QThread):
    """
    파일 시스템 인덱싱 워커 스레드
    [프로세스]
    1. 지정된 경로들을 순회하며 파일 정보 수집
    2. SQLite 데이터베이스에 파일 메타데이터 저장
    3. 진행 상황을 시그널로 전달
    4. 인덱싱 완료 시 총 파일 수 반환
    """
    # 시그널 정의 -----------------------------------------------------------------
    progress_update = Signal(str, int, int)                             # 메시지, 현재 진행, 전체
    indexing_finished = Signal(int)                                     # 인덱싱된 파일 수
    
    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path                                          # 데이터베이스 파일 경로
        self.should_stop = False                                        # 중지 플래그
        self.index_paths = []                                           # 인덱싱할 경로 목록
    
    def stop(self):
        """
        인덱싱 중지 요청
        [프로세스]
        1. should_stop 플래그를 True로 설정
        2. 현재 진행 중인 작업이 다음 체크포인트에서 중단됨
        """
        self.should_stop = True
    
    def run(self):
        """
        인덱싱 메인 실행 함수
        [프로세스]
        1. 데이터베이스 초기화 및 테이블 생성
        2. 지정된 경로들을 순회하며 파일 정보 수집
        3. 수집된 정보를 데이터베이스에 저장
        4. 완료 시그널 발송
        """
        self.should_stop = False
        total_indexed = 0
        
        try:
            # DB 연결
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 테이블 생성
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS file_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    directory TEXT NOT NULL,
                    is_dir INTEGER NOT NULL,
                    size INTEGER,
                    modified_time TEXT,
                    indexed_time TEXT,
                    name_lower TEXT,
                    extension TEXT
                )
            ''')
            
            # 인덱스 생성 (검색 성능 향상)
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_name_lower ON file_index(name_lower)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_extension ON file_index(extension)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_directory ON file_index(directory)')
            
            conn.commit()
            
            # 기존 인덱스 삭제
            cursor.execute('DELETE FROM file_index')
            conn.commit()
            
            # 각 경로 인덱싱
            for base_path in self.index_paths:
                if self.should_stop:
                    break
                
                if not os.path.exists(base_path):
                    continue
                
                self.progress_update.emit(f"인덱싱 준비: {base_path}", 0, 100)
                
                # 전체 파일 수 예측
                estimated_count = 0
                for root, dirs, files in os.walk(base_path):
                    estimated_count += len(dirs) + len(files)
                    if estimated_count > 1000:  # 예측만 하므로 일부만 카운트
                        estimated_count = max(estimated_count, 10000)
                        break
                
                processed = 0
                
                # 실제 인덱싱
                for root, dirs, files in os.walk(base_path):
                    if self.should_stop:
                        break
                    
                    # 디렉토리와 파일 모두 인덱싱
                    all_items = [(d, True) for d in dirs] + [(f, False) for f in files]
                    
                    for name, is_dir in all_items:
                        if self.should_stop:
                            break
                        
                        try:
                            full_path = os.path.join(root, name)
                            stat_info = os.stat(full_path)
                            
                            # 확장자 추출
                            ext = ""
                            if not is_dir:
                                _, ext = os.path.splitext(name)
                                ext = ext.lower()
                            
                            # DB에 삽입
                            cursor.execute('''
                                INSERT OR REPLACE INTO file_index 
                                (name, path, directory, is_dir, size, modified_time, indexed_time, name_lower, extension)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                name,
                                full_path,
                                root,
                                1 if is_dir else 0,
                                stat_info.st_size if not is_dir else 0,
                                datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                name.lower(),
                                ext
                            ))
                            
                            total_indexed += 1
                            processed += 1
                            
                            # 주기적으로 커밋 (성능 최적화)
                            if total_indexed % 1000 == 0:
                                conn.commit()
                                progress = int((processed / max(estimated_count, 1)) * 100)
                                self.progress_update.emit(
                                    f"인덱싱 중: {root}",
                                    min(progress, 99),
                                    100
                                )
                        
                        except Exception as e:
                            # 개별 파일 오류는 무시하고 계속 진행
                            pass
                    
                    # 배치 커밋
                    if total_indexed % 5000 == 0:
                        conn.commit()
            
            # 최종 커밋
            conn.commit()
            conn.close()
            
            if not self.should_stop:
                self.indexing_finished.emit(total_indexed)
            
        except Exception as e:
            self.progress_update.emit(f"인덱싱 오류: {str(e)}", 0, 100)
            self.indexing_finished.emit(0)


class SearchWorker(QThread):
    """백그라운드에서 파일 검색을 수행하는 워커 스레드"""
    result_found = Signal(dict)  # 검색 결과 발견 시그널
    search_finished = Signal(int)  # 검색 완료 시그널 (총 결과 수)
    # 시그널 정의 -----------------------------------------------------------------
    progress_update = Signal(str)                                       # 진행 상황 업데이트
    
    def __init__(self):
        super().__init__()
        # 검색 경로 및 쿼리 설정 -------------------------------------------------
        self.search_paths = []                                          # 검색할 경로 목록 (다중 경로 지원)
        self.search_query = ""                                          # 검색 키워드
        
        # 검색 옵션 설정 ---------------------------------------------------------
        self.search_content = False                                     # 파일 내용 검색 여부
        self.case_sensitive = False                                     # 대소문자 구분
        self.flexible_word_match = False                                # 단어 순서 무시
        self.file_type_filter = ""                                      # 파일 형식 필터
        
        # 크기 필터 설정 ---------------------------------------------------------
        self.min_size = 0                                               # 최소 크기 (bytes)
        self.max_size = 0                                               # 최대 크기 (0 = 무제한)
        
        # 날짜 필터 설정 ---------------------------------------------------------
        self.date_filter = None                                         # 'today', 'week', 'month', 'year', None
        
        # 인덱스 검색 설정 -------------------------------------------------------
        self.use_index = False                                          # 인덱스 사용 여부
        self.db_path = ""                                               # 데이터베이스 경로
        
        # 실행 상태 변수 ---------------------------------------------------------
        self.is_running = False                                         # 실행 중 플래그
        self.should_stop = False                                        # 중지 플래그
        self.results_count = 0                                          # 검색 결과 수
    
    def stop(self):
        """
        검색 중지 요청
        [프로세스]
        1. should_stop 플래그를 True로 설정
        2. 현재 진행 중인 검색이 다음 체크포인트에서 중단됨
        """
        self.should_stop = True
    
    def run(self):
        """
        검색 메인 실행 함수
        [프로세스]
        1. 인덱스 사용 가능 여부 확인
        2. 인덱스 기반 또는 파일시스템 기반 검색 실행
        3. 검색 결과를 시그널로 전달
        4. 완료 시그널 발송
        """
        self.is_running = True
        self.should_stop = False
        self.results_count = 0
        
        if not self.search_paths:
            self.search_finished.emit(0)
            self.is_running = False
            return
        
        # 인덱스 사용 가능 여부 확인
        if self.use_index and self.db_path and os.path.exists(self.db_path):
            self._search_with_index()
        else:
            self._search_filesystem()
        
        self.search_finished.emit(self.results_count)
        self.is_running = False
    
    def _search_with_index(self):
        """인덱스 기반 빠른 검색"""
        try:
            self.progress_update.emit("인덱스에서 검색 중...")
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # SQL 쿼리 구성
            where_clauses = []
            params = []
            
            # 검색어 처리
            if self.flexible_word_match:
                # 단어 순서 무시: 각 단어를 개별적으로 검색
                import re
                words = re.split(r'[\s_\-\.]+', self.search_query)
                words = [w for w in words if w]
                
                for word in words:
                    if self.case_sensitive:
                        where_clauses.append("name LIKE ?")
                        params.append(f"%{word}%")
                    else:
                        where_clauses.append("name_lower LIKE ?")
                        params.append(f"%{word.lower()}%")
            else:
                # 일반 검색
                if self.case_sensitive:
                    where_clauses.append("name LIKE ?")
                    params.append(f"%{self.search_query}%")
                else:
                    where_clauses.append("name_lower LIKE ?")
                    params.append(f"%{self.search_query.lower()}%")
            
            # 검색 경로 필터 (여러 경로 지원)
            if len(self.search_paths) == 1:
                where_clauses.append("(path LIKE ? OR directory LIKE ?)")
                search_path_pattern = f"{self.search_paths[0]}%"
                params.extend([search_path_pattern, search_path_pattern])
            else:
                # 여러 경로
                path_conditions = []
                for search_path in self.search_paths:
                    path_conditions.append("(path LIKE ? OR directory LIKE ?)")
                    search_path_pattern = f"{search_path}%"
                    params.extend([search_path_pattern, search_path_pattern])
                where_clauses.append(f"({' OR '.join(path_conditions)})")
            
            # 파일 타입 필터
            if self.file_type_filter:
                where_clauses.append("extension = ?")
                params.append(f".{self.file_type_filter.lower()}")
            
            # 크기 필터
            if self.min_size > 0:
                where_clauses.append("size >= ?")
                params.append(self.min_size)
            if self.max_size > 0:
                where_clauses.append("size <= ?")
                params.append(self.max_size)
            
            # 날짜 필터
            if self.date_filter:
                now = datetime.now()
                if self.date_filter == 'today':
                    threshold = now - timedelta(days=1)
                elif self.date_filter == 'week':
                    threshold = now - timedelta(weeks=1)
                elif self.date_filter == 'month':
                    threshold = now - timedelta(days=30)
                elif self.date_filter == 'year':
                    threshold = now - timedelta(days=365)
                else:
                    threshold = None
                
                if threshold:
                    where_clauses.append("modified_time >= ?")
                    params.append(threshold.strftime('%Y-%m-%d %H:%M:%S'))
            
            # 최종 쿼리
            query = f"SELECT name, path, directory, is_dir, size, modified_time FROM file_index WHERE {' AND '.join(where_clauses)} LIMIT 10000"
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            for row in results:
                if self.should_stop:
                    break
                
                name, path, directory, is_dir, size, modified_time = row
                
                # 파일이 여전히 존재하는지 확인
                if os.path.exists(path):
                    result = {
                        'name': name,
                        'path': path,
                        'dir': directory,
                        'is_dir': bool(is_dir),
                        'size': size or 0,
                        'modified': modified_time,
                        'match_type': 'name'
                    }
                    self.result_found.emit(result)
                    self.results_count += 1
            
            conn.close()
            
        except Exception as e:
            self.progress_update.emit(f"인덱스 검색 오류: {str(e)}")
            # 오류 시 파일 시스템 검색으로 폴백
            self._search_filesystem()
    
    def _search_filesystem(self):
        """파일 시스템 직접 검색 (기존 방식)"""
        # 날짜 필터 계산
        date_threshold = None
        if self.date_filter:
            now = datetime.now()
            if self.date_filter == 'today':
                date_threshold = now - timedelta(days=1)
            elif self.date_filter == 'week':
                date_threshold = now - timedelta(weeks=1)
            elif self.date_filter == 'month':
                date_threshold = now - timedelta(days=30)
            elif self.date_filter == 'year':
                date_threshold = now - timedelta(days=365)
        
        try:
            # 여러 경로 검색
            for search_path in self.search_paths:
                if self.should_stop:
                    break
                
                if not os.path.exists(search_path):
                    continue
                
                for root, dirs, files in os.walk(search_path):
                    if self.should_stop:
                        break
                    
                    self.progress_update.emit(f"검색 중: {root}")
                    
                    # 디렉토리 + 파일 모두 검색
                    all_items = [(d, True) for d in dirs] + [(f, False) for f in files]
                    
                    for name, is_dir in all_items:
                        if self.should_stop:
                            break
                        
                        full_path = os.path.join(root, name)
                        
                        # 파일명 매칭
                        if self.flexible_word_match:
                            # 단어 순서 무시 모드: 모든 단어가 파일명에 포함되어 있는지 확인
                            import re
                            # 공백, 언더스코어, 하이픈 등으로 단어 분리
                            query_words = re.split(r'[\s_\-\.]+', self.search_query)
                            query_words = [w for w in query_words if w]  # 빈 문자열 제거
                            
                            if self.case_sensitive:
                                name_match = all(word in name for word in query_words)
                            else:
                                name_lower = name.lower()
                                name_match = all(word.lower() in name_lower for word in query_words)
                        else:
                            # 일반 모드: 순서대로 포함 여부 확인
                            if self.case_sensitive:
                                name_match = self.search_query in name
                            else:
                                name_match = self.search_query.lower() in name.lower()
                        
                        # 파일 타입 필터
                        if self.file_type_filter and not is_dir:
                            if not fnmatch.fnmatch(name.lower(), f"*.{self.file_type_filter.lower()}"):
                                name_match = False
                        
                        # 크기 필터 (파일만)
                        if not is_dir and name_match:
                            try:
                                file_size = os.path.getsize(full_path)
                                if self.min_size > 0 and file_size < self.min_size:
                                    name_match = False
                                if self.max_size > 0 and file_size > self.max_size:
                                    name_match = False
                            except:
                                pass
                        
                        # 날짜 필터
                        if name_match and date_threshold:
                            try:
                                mod_time = datetime.fromtimestamp(os.path.getmtime(full_path))
                                if mod_time < date_threshold:
                                    name_match = False
                            except:
                                pass
                        
                        # 내용 검색 (텍스트 파일만)
                        content_match = False
                        if self.search_content and not is_dir and name_match:
                            try:
                                # 텍스트 파일만 검색 (크기 제한: 10MB 이하)
                                if os.path.getsize(full_path) < 10 * 1024 * 1024:
                                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                        content = f.read()
                                        if self.case_sensitive:
                                            content_match = self.search_query in content
                                        else:
                                            content_match = self.search_query.lower() in content.lower()
                            except:
                                pass
                        
                        # 결과 발견
                        if name_match or content_match:
                            try:
                                stat_info = os.stat(full_path)
                                result = {
                                    'name': name,
                                    'path': full_path,
                                    'dir': root,
                                    'is_dir': is_dir,
                                    'size': stat_info.st_size if not is_dir else 0,
                                    'modified': datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                                    'match_type': 'content' if content_match else 'name'
                                }
                                self.result_found.emit(result)
                                self.results_count += 1
                            except:
                                pass
        
        except Exception as e:
            self.progress_update.emit(f"검색 오류: {str(e)}")


class FileSizeWorker(QThread):
    """선택된 파일의 크기를 백그라운드에서 계산하는 워커"""
    size_ready = Signal(int, str, int)  # request_id, file_path, size

    def __init__(self, request_id, file_path, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.file_path = file_path

    def run(self):
        try:
            size = os.path.getsize(self.file_path)
        except Exception:
            size = -1
        self.size_ready.emit(self.request_id, self.file_path, size)


# ====================================================================================
# 3. 커스텀 UI 컴포넌트 클래스 (Custom UI Components)
# ====================================================================================
    # 파일 탐색기의 사용자 정의 UI 컴포넌트들입니다.
    # - CustomTreeView: 파일/폴더 트리뷰
    # - FileSystemModel: 파일 시스템 모델 (메모 기능 포함)
    # - FileSystemSortProxyModel: 정렬 프록시 모델

class CustomTreeView(QTreeView):
    """
    커스텀 파일 트리뷰
    [기능]
    1. 백스페이스 키로 상위 폴더 이동
    2. 엔터 키로 폴더 열기
    3. 드래그 앤 드롭으로 파일/폴더 이동 및 복사
    4. 커스텀 화살표 렌더링
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # 콜백 함수 설정 ---------------------------------------------------------
        self.navigate_back_callback = None                              # 뒤로 가기 콜백
        self.open_item_callback = None                                  # 아이템 열기 콜백
        self.drop_callback = None                                       # 드롭 콜백
        
        # 참조 변수 ---------------------------------------------------------------
        self.side = None                                                # 'left' 또는 'right'
        self.parent_window = None                                       # 부모 윈도우 참조
        
        # 드래그 앤 드롭 설정 -----------------------------------------------------
        self.setAcceptDrops(True)                                       # 드롭 허용
        self.setDragEnabled(True)                                       # 드래그 허용
        self.setDragDropMode(QAbstractItemView.DragDrop)               # 드래그앤드롭 모드
        self.setDefaultDropAction(Qt.MoveAction)                        # 기본 동작: 이동
    
    def drawBranches(self, painter, rect, index):
        """트리뷰 화살표를 커스텀 색상으로 그리기 (테마·선택 상태 반영)"""
        # 기본 브랜치 그리기는 건너뛰기 (우리가 직접 그릴 것이므로)
        # super().drawBranches(painter, rect, index)
        
        # 자식이 있는 항목에만 화살표 그리기
        if self.model() and self.model().hasChildren(index):
            painter.save()

            # ── 테마·선택 상태에 따른 화살표 색상 결정 ─────────────────
            is_dark = (
                self.parent_window is not None
                and hasattr(self.parent_window, 'current_theme')
                and self.parent_window.current_theme == 'dark'
            )
            is_selected = (
                self.selectionModel() is not None
                and self.selectionModel().isSelected(index)
            )

            if is_dark:
                if is_selected:
                    # 다크 테마 + 선택됨: 하늘색(#2a5090) 배경 위 → 흰색 화살표
                    arrow_color = QColor("#ffffff")
                else:
                    # 다크 테마 + 미선택: 밝은 하늘색 화살표
                    arrow_color = QColor("#7ab0e0")
            else:
                if is_selected:
                    # 라이트 테마 + 선택됨: 연한 하늘색(#d6e9f7) 배경 위 → 진한 네이비
                    arrow_color = QColor("#1a365d")
                else:
                    # 라이트 테마 + 미선택: 기존 네이비
                    arrow_color = QColor("#2c5282")

            painter.setPen(QPen(arrow_color, 2))
            painter.setBrush(arrow_color)
            # ─────────────────────────────────────────────────────────────

            # 실제 아이템의 시각적 위치 가져오기
            item_rect = self.visualRect(index)
            
            # 화살표를 아이콘 바로 왼쪽에 위치시키기
            # item_rect.left()는 아이콘이 시작되는 위치이므로 그 왼쪽에 화살표를 그림
            arrow_x = item_rect.left() - 14  # 아이콘 왼쪽 14픽셀
            arrow_y = rect.top() + rect.height() / 2
            arrow_size = 6
            
            # 접혀있는지 확인
            is_expanded = self.isExpanded(index)
            
            # 화살표 경로 생성
            path = QPainterPath()
            if is_expanded:
                # 아래 화살표 (▼)
                path.moveTo(arrow_x - arrow_size/2, arrow_y - arrow_size/3)
                path.lineTo(arrow_x + arrow_size/2, arrow_y - arrow_size/3)
                path.lineTo(arrow_x, arrow_y + arrow_size/2)
                path.closeSubpath()
            else:
                # 오른쪽 화살표 (▶)
                path.moveTo(arrow_x - arrow_size/3, arrow_y - arrow_size/2)
                path.lineTo(arrow_x + arrow_size/2, arrow_y)
                path.lineTo(arrow_x - arrow_size/3, arrow_y + arrow_size/2)
                path.closeSubpath()
            
            painter.drawPath(path)
            painter.restore()
    
    def mousePressEvent(self, event):
        """마우스 클릭 시 활성 사이드 업데이트"""
        super().mousePressEvent(event)
        # 부모 윈도우에 활성 사이드 업데이트 요청
        if self.parent_window and self.side:
            if self.side == 'left':
                self.parent_window.active_tab_widget = self.parent_window.left_tabs
                self.parent_window.active_side = 'left'
            elif self.side == 'right':
                self.parent_window.active_tab_widget = self.parent_window.right_tabs
                self.parent_window.active_side = 'right'
            self.parent_window.update_active_panel_highlight()
    
    def keyPressEvent(self, event):
        """
        키 입력 이벤트 처리
        [프로세스]
        1. 편집 모드 확인 (메모 컬럼 편집 중인지 체크)
        2. 백스페이스: 상위 폴더로 이동
        3. 엔터: 폴더 열기/파일 실행 (편집 모드가 아닐 때만)
        """
        # 현재 편집 중인지 확인 (메모 컬럼 편집 등)
        if self.state() == QAbstractItemView.EditingState:
            # 편집 중이면 기본 동작 수행 (엔터로 편집 완료)
            super().keyPressEvent(event)
            return
        
        if event.key() == Qt.Key_Backspace:
            # 백스페이스 키로 뒤로 가기
            if self.navigate_back_callback:
                self.navigate_back_callback()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # 엔터 키로 폴더 열기 또는 파일 실행 (다중 선택 지원)
            if self.open_item_callback:
                # 선택된 모든 항목 가져오기 (컬럼 0만 처리, 중복 행 방지)
                selected = self.selectedIndexes()
                col0_indices = [idx for idx in selected if idx.column() == 0]
                if not col0_indices:
                    # 선택 없으면 현재 포커스 항목만 처리
                    index = self.currentIndex()
                    if index.isValid():
                        self.open_item_callback(index)
                elif len(col0_indices) == 1:
                    # 단일 선택: 기존 동작 (폴더 이동 or 파일 열기)
                    self.open_item_callback(col0_indices[0])
                else:
                    # 다중 선택: 폴더는 첫 번째만 navigate, 파일은 모두 열기
                    folder_opened = False
                    for idx in col0_indices:
                        # 소스 경로를 직접 확인하기 위해 콜백 대신 모델 접근
                        # (open_item_callback이 폴더면 navigate하므로
                        #  폴더가 여럿이면 첫 번째만 navigate)
                        from PySide6.QtWidgets import QFileSystemModel as _QFM
                        model = self.model()
                        # 프록시 모델일 경우 소스로 변환
                        if hasattr(model, 'mapToSource'):
                            src_idx = model.mapToSource(idx)
                            src_model = model.sourceModel()
                            path = src_model.filePath(src_idx)
                        else:
                            path = model.filePath(idx)
                        import os as _os
                        if _os.path.isdir(path):
                            if not folder_opened:
                                self.open_item_callback(idx)
                                folder_opened = True
                            # 두 번째 이후 폴더는 무시
                        else:
                            # 파일은 모두 열기
                            self.open_item_callback(idx)
        else:
            super().keyPressEvent(event)
    
    def dragEnterEvent(self, event):
        """드래그 시작 시"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)
    
    def dragMoveEvent(self, event):
        """드래그 중 이동"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
    
    def dropEvent(self, event):
        """드롭 시 파일/폴더 이동 또는 복사"""
        if self.drop_callback and event.mimeData().hasUrls():
            # 드롭 위치의 인덱스 가져오기
            drop_index = self.indexAt(event.pos())
            
            # Ctrl 키가 눌려있으면 복사, 아니면 이동
            is_copy = event.keyboardModifiers() & Qt.ControlModifier
            
            # 콜백 함수 호출
            self.drop_callback(event.mimeData(), drop_index, is_copy)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class AddressBar(QLineEdit):
    """클릭 시 전체 텍스트 선택되는 커스텀 주소창"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.first_click = True
    
    def mousePressEvent(self, event):
        """마우스 클릭 시 전체 선택"""
        super().mousePressEvent(event)
        if self.first_click:
            self.selectAll()
            self.first_click = False
    
    def focusInEvent(self, event):
        """포커스를 받을 때 전체 선택"""
        super().focusInEvent(event)
        self.selectAll()
        self.first_click = True
    
    def focusOutEvent(self, event):
        """포커스를 잃을 때 첫 클릭 플래그 리셋"""
        super().focusOutEvent(event)
        self.first_click = True


class AnimatedRefreshButton(QPushButton):
    """회전 애니메이션이 있는 새로고침 버튼"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._rotation = 0
        self._animation = None
        self._original_icon = None
        
    def get_rotation(self):
        return self._rotation
    
    def set_rotation(self, angle):
        self._rotation = angle
        self.update()  # 버튼 다시 그리기
    
    # QPropertyAnimation에서 사용할 프로퍼티 정의
    rotation = Property(int, get_rotation, set_rotation)
    
    def paintEvent(self, event):
        """버튼을 회전시켜서 그리기"""
        if self._rotation == 0:
            # 회전이 없으면 기본 그리기
            super().paintEvent(event)
        else:
            # 회전 적용
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            
            # 버튼 중심점 계산
            center = self.rect().center()
            
            # 회전 변환 적용
            painter.translate(center)
            painter.rotate(self._rotation)
            painter.translate(-center)
            
            # 원래 버튼 그리기
            super().paintEvent(event)
            painter.end()
    
    def start_rotation_animation(self):
        """회전 애니메이션 시작"""
        if self._animation and self._animation.state() == QPropertyAnimation.Running:
            return  # 이미 실행 중이면 무시
        
        self._animation = QPropertyAnimation(self, b"rotation")
        self._animation.setDuration(600)  # 0.6초
        self._animation.setStartValue(0)
        self._animation.setEndValue(360)
        self._animation.finished.connect(self._on_animation_finished)
        self._animation.start()
    
    def _on_animation_finished(self):
        """애니메이션 완료 후 초기화"""
        self._rotation = 0
        self.update()


class DraggableTabBar(QTabBar):
    """드래그 가능한 커스텀 TabBar"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.drag_start_pos = None
        self.drag_start_index = -1
    
    def mousePressEvent(self, event):
        """마우스 클릭 시 드래그 준비"""
        if event.button() == Qt.LeftButton:
            self.drag_start_index = self.tabAt(event.pos())
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        """마우스 드래그 중"""
        if (event.buttons() & Qt.LeftButton and 
            self.drag_start_index >= 0 and 
            self.drag_start_pos is not None):
            
            # 드래그 거리 확인 (20픽셀 이상)
            if (event.pos() - self.drag_start_pos).manhattanLength() > 20:
                # 부모 TabWidget의 드래그 시작
                tab_widget = self.parent()
                if isinstance(tab_widget, DraggableTabWidget):
                    tab_widget.start_tab_drag(self.drag_start_index)
                    # 드래그 시작 후 초기화
                    self.drag_start_index = -1
                    self.drag_start_pos = None
                    return
        
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        """마우스 릴리즈"""
        self.drag_start_index = -1
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)


class DraggableTabWidget(QTabWidget):
    """탭을 드래그하여 다른 TabWidget으로 이동할 수 있는 커스텀 TabWidget"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.parent_window = None
        self.side = None
        
        # 커스텀 TabBar 설정
        custom_tab_bar = DraggableTabBar(self)
        self.setTabBar(custom_tab_bar)
    
    def start_tab_drag(self, index):
        """탭 드래그 시작"""
        from PySide6.QtCore import QMimeData, QByteArray
        from PySide6.QtGui import QDrag
        
        tab_text = self.tabText(index)
        widget = self.widget(index)
        
        # MIME 데이터 생성
        mime_data = QMimeData()
        
        # 탭 정보를 JSON으로 직렬화
        import json
        tab_data = {
            'tab_text': tab_text,
            'tab_index': index,
            'source_side': self.side,
            'current_path': widget.current_path if hasattr(widget, 'current_path') else None
        }
        mime_data.setData('application/x-tab-data', QByteArray(json.dumps(tab_data).encode('utf-8')))
        
        # 드래그 객체 생성
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        
        # 드래그 실행
        result = drag.exec_(Qt.MoveAction)
        
        if result == Qt.MoveAction:
            # 드래그 성공 시 원본 탭 제거는 dropEvent에서 처리
            pass
    
    def dragEnterEvent(self, event):
        """드래그 진입"""
        if event.mimeData().hasFormat('application/x-tab-data'):
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dragMoveEvent(self, event):
        """드래그 이동"""
        if event.mimeData().hasFormat('application/x-tab-data'):
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dropEvent(self, event):
        """드롭 처리"""
        if not event.mimeData().hasFormat('application/x-tab-data'):
            event.ignore()
            return
        
        # 탭 데이터 파싱
        import json
        data_bytes = event.mimeData().data('application/x-tab-data')
        tab_data = json.loads(bytes(data_bytes).decode('utf-8'))
        
        source_side = tab_data['source_side']
        source_index = tab_data['tab_index']
        current_path = tab_data['current_path']
        
        # 같은 TabWidget 내에서의 드래그는 무시 (기본 Qt 동작 사용)
        if source_side == self.side:
            event.ignore()
            return
        
        # 부모 윈도우에서 탭 이동 처리
        if self.parent_window:
            self.parent_window.move_tab_between_sides(source_side, source_index, self.side)
            event.acceptProposedAction()
        else:
            event.ignore()


class MemoDelegate(QLineEdit):
    """
    메모 컬럼 전용 델리게이트
    [기능]
    1. 메모 입력 시 충분한 너비 확보
    2. 입력 중인 텍스트가 잘리지 않도록 함
    3. 엔터키로 편집 완료
    """
    def __init__(self, parent=None):
        from PySide6.QtWidgets import QStyledItemDelegate
        super(QStyledItemDelegate, self).__init__(parent)
    
    def createEditor(self, parent, option, index):
        """커스텀 에디터 생성"""
        from PySide6.QtWidgets import QStyledItemDelegate
        editor = QLineEdit(parent)
        # 에디터 크기를 충분히 크게 설정
        editor.setMinimumWidth(300)
        # 텍스트가 잘리지 않도록 여백 추가
        editor.setStyleSheet("""
            QLineEdit {
                padding: 4px 8px;
                border: 2px solid #4a90e2;
                border-radius: 3px;
                background: #ffffff;
                font-size: 9pt;
            }
        """)
        return editor
    
    def setEditorData(self, editor, index):
        """에디터에 데이터 설정"""
        value = index.model().data(index, Qt.EditRole)
        editor.setText(str(value) if value else "")
    
    def setModelData(self, editor, model, index):
        """모델에 데이터 설정"""
        model.setData(index, editor.text(), Qt.EditRole)
    
    def updateEditorGeometry(self, editor, option, index):
        """에디터 위치 및 크기 조정"""
        # 셀보다 넓게 표시
        rect = option.rect
        rect.setWidth(max(300, rect.width()))
        editor.setGeometry(rect)


# QStyledItemDelegate를 상속하는 올바른 MemoDelegate
from PySide6.QtWidgets import QStyledItemDelegate

class MemoItemDelegate(QStyledItemDelegate):
    """
    메모 컬럼 전용 델리게이트 (올바른 구현)
    [기능]
    1. 메모 입력 시 충분한 너비 확보
    2. 입력 중인 텍스트가 잘리지 않도록 함
    3. 엔터키로 편집 완료
    4. 다크/라이트 테마 지원
    """
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window  # 테마 정보 확인을 위해 메인 윈도우 참조
    
    def createEditor(self, parent, option, index):
        """커스텀 에디터 생성"""
        editor = QLineEdit(parent)
        # 에디터 크기를 충분히 크게 설정
        editor.setMinimumWidth(300)
        editor.setMinimumHeight(28)  # 충분한 높이 확보
        
        # 테마에 따라 스타일 설정
        current_theme = 'light'
        if self.main_window and hasattr(self.main_window, 'current_theme'):
            current_theme = self.main_window.current_theme
        
        if current_theme == 'dark':
            editor.setStyleSheet("""
                QLineEdit {
                    padding: 2px 8px;
                    border: 2px solid #3a6ea5;
                    border-radius: 3px;
                    background: #1e1e1e;
                    color: #e6eef8;
                    font-size: 8pt;
                    line-height: 1.2;
                }
            """)
        else:
            editor.setStyleSheet("""
                QLineEdit {
                    padding: 2px 8px;
                    border: 2px solid #4a90e2;
                    border-radius: 3px;
                    background: #ffffff;
                    color: #1a365d;
                    font-size: 8pt;
                    line-height: 1.2;
                }
            """)
        
        # 텍스트 정렬을 수직 중앙으로 설정
        editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return editor
    
    def setEditorData(self, editor, index):
        """에디터에 데이터 설정"""
        value = index.model().data(index, Qt.EditRole)
        editor.setText(str(value) if value else "")
    
    def setModelData(self, editor, model, index):
        """모델에 데이터 설정"""
        model.setData(index, editor.text(), Qt.EditRole)
    
    def updateEditorGeometry(self, editor, option, index):
        """에디터 위치 및 크기 조정"""
        # 셀보다 넓게 표시하고 높이도 충분히 확보
        rect = option.rect
        rect.setWidth(max(300, rect.width()))
        # 에디터 높이를 셀 높이보다 크게 설정하여 텍스트가 잘 보이도록 함
        rect.setHeight(max(28, rect.height()))
        editor.setGeometry(rect)


class NameItemDelegate(QStyledItemDelegate):
    """
    Name 컬럼(0) 전용 델리게이트 — F2 인라인 이름 편집 시 텍스트가 잘리지 않도록 함
    [기능]
    1. 이름 편집 에디터의 높이·패딩을 충분히 확보
    2. 테마(라이트/다크)에 따라 글자색·배경색 명시적 지정
    3. 라이트 테마에서 전역 QLineEdit 패딩으로 인해 텍스트가 잘리는 문제 해결
    """
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window

    def createEditor(self, parent, option, index):
        """커스텀 에디터 생성 — 충분한 높이와 명시적 스타일 적용"""
        editor = QLineEdit(parent)
        editor.setMinimumHeight(28)

        current_theme = 'light'
        if self.main_window and hasattr(self.main_window, 'current_theme'):
            current_theme = self.main_window.current_theme

        if current_theme == 'dark':
            editor.setStyleSheet("""
                QLineEdit {
                    padding: 2px 6px;
                    border: 2px solid #3a6ea5;
                    border-radius: 3px;
                    background: #1e1e1e;
                    color: #e6eef8;
                    font-size: 9pt;
                }
            """)
        else:
            editor.setStyleSheet("""
                QLineEdit {
                    padding: 2px 6px;
                    border: 2px solid #4a90e2;
                    border-radius: 3px;
                    background: #ffffff;
                    color: #1a365d;
                    font-size: 9pt;
                }
            """)

        editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return editor

    def setEditorData(self, editor, index):
        """에디터에 현재 파일명 설정"""
        value = index.model().data(index, Qt.EditRole)
        editor.setText(str(value) if value else "")

    def setModelData(self, editor, model, index):
        """편집 완료 시 모델에 데이터 설정"""
        model.setData(index, editor.text(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        """에디터 위치 및 크기 조정 — 높이를 셀보다 충분히 확보"""
        rect = option.rect
        rect.setHeight(max(28, rect.height()))
        editor.setGeometry(rect)


class FileSystemModel(QFileSystemModel):
    """
    커스텀 파일 시스템 모델
    [기능]
    1. 숨김 파일 표시 옵션 지원
    2. 파일/폴더별 메모 기능
    3. 표준 4개 컬럼 + 메모 컬럼 (Name, Size, Type, Date, Memo)
    4. 파일 변경 감지 및 자동 업데이트
    """
    def __init__(self, shared_memos=None, memo_changed_callback=None, rename_done_callback=None):
        super().__init__()
        # 모델 옵션 설정 ---------------------------------------------------------
        self.show_hidden = False                                        # 숨김 파일 표시 여부
        # 공유 메모 딕셔너리 사용 (모든 탭이 동일한 메모 데이터 공유)
        self.file_memos = shared_memos if shared_memos is not None else {}
        self.memo_changed_callback = memo_changed_callback              # 메모 변경 시 호출될 콜백
        self.rename_done_callback = rename_done_callback                # 이름 변경 완료 시 호출될 콜백
        
        # 파일 변경 감지 시그널 연결 -----------------------------------------
        # directoryLoaded: 디렉토리가 로드될 때
        # fileRenamed: 파일명이 변경될 때  
        # rootPathChanged: 루트 경로가 변경될 때
        self.directoryLoaded.connect(self._on_directory_loaded)

    # 경로를 일관되게 정규화 (윈도우/유닉스 슬래시 혼용 문제 방지)
    def _norm(self, path):
        return os.path.normpath(path) if path else path
        
    def columnCount(self, parent=None):
        """컬럼 개수를 5개로 확장 (Name, Size, Type, Date Modified, Memo)"""
        return super().columnCount(parent) + 1
    
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """헤더 데이터 - 메모 컬럼 추가 및 컬럼 이름 커스터마이징"""
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section == 0:  # Name 컬럼
                return "Name(F2)"
            if section == 4:  # 메모 컬럼
                return "Memo(F3)"
        return super().headerData(section, orientation, role)
    
    def data(self, index, role=Qt.DisplayRole):
        """데이터 반환 - 메모 컬럼 처리"""
        if not index.isValid():
            return None
        
        if index.column() == 4:  # 메모 컬럼
            if role == Qt.DisplayRole or role == Qt.EditRole:
                raw_path = self.filePath(self.index(index.row(), 0, index.parent()))
                norm_path = self._norm(raw_path)
                # 우선 정규화된 경로 조회, 없으면 원본 키 조회 (기존 슬래시 방식 호환)
                if norm_path in self.file_memos:
                    return self.file_memos.get(norm_path, "")
                return self.file_memos.get(raw_path, "")
            elif role == Qt.TextAlignmentRole:
                return Qt.AlignLeft | Qt.AlignVCenter
        
        return super().data(index, role)
    
    def setData(self, index, value, role=Qt.EditRole):
        """데이터 설정 - Name 컬럼(인라인 이름 변경) 및 메모 컬럼 편집"""
        if index.column() == 0 and role == Qt.EditRole:
            # ── Name 컬럼 인라인 편집: 실제 파일/폴더 이름 변경 ──────────────
            new_name = (value or "").strip()
            if not new_name:
                return False
            old_path = self.filePath(index)
            old_name = os.path.basename(old_path)
            if new_name == old_name:
                return False  # 변경 없음
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
            except Exception:
                return False
            # 메모 키 이동
            self.move_memo(old_path, new_path)
            # 이름 변경 완료 콜백 (undo 스택 등록 등)
            if self.rename_done_callback:
                self.rename_done_callback(old_path, new_path)
            self.dataChanged.emit(index, index)
            return True

        if index.column() == 4 and role == Qt.EditRole:
            raw_path = self.filePath(self.index(index.row(), 0, index.parent()))
            norm_path = self._norm(raw_path)
            target_key = norm_path if norm_path else raw_path
            if value:
                self.file_memos[target_key] = str(value)
            else:
                # 빈 문자열이면 메모 삭제 (양쪽 키 모두 제거 시도)
                self.file_memos.pop(target_key, None)
                self.file_memos.pop(raw_path, None)
            self.dataChanged.emit(index, index)
            
            # 메모 변경 콜백 호출 (다른 탭들도 업데이트하도록)
            if self.memo_changed_callback:
                self.memo_changed_callback(target_key)
            
            return True
        return super().setData(index, value, role)
    
    def flags(self, index):
        """플래그 설정 - Name 컬럼(0)과 메모 컬럼(4)을 편집 가능하게"""
        flags = super().flags(index)
        if index.column() == 0:  # Name 컬럼 — 인라인 이름 편집
            flags |= Qt.ItemIsEditable
        if index.column() == 4:  # 메모 컬럼
            flags |= Qt.ItemIsEditable
        return flags
    
    def setShowHidden(self, show):
        self.show_hidden = show
        if show:
            self.setFilter(QFileSystemModel.AllDirs | QFileSystemModel.Files | QFileSystemModel.Hidden)
        else:
            self.setFilter(QFileSystemModel.AllDirs | QFileSystemModel.Files)
    
    def set_memo(self, file_path, memo):
        """메모 설정"""
        norm_path = self._norm(file_path)
        if memo:
            self.file_memos[norm_path] = memo
        else:
            self.file_memos.pop(norm_path, None)
    
    def get_memo(self, file_path):
        """메모 가져오기"""
        norm_path = self._norm(file_path)
        if norm_path in self.file_memos:
            return self.file_memos.get(norm_path, "")
        # 기존 키가 원본 경로로 저장된 경우를 대비해 호환 처리
        return self.file_memos.get(file_path, "")
    
    def move_memo(self, old_path, new_path):
        """파일 이동 시 메모도 함께 이동"""
        old_norm = self._norm(old_path)
        new_norm = self._norm(new_path)
        # 우선 정규화된 키, 없으면 원본 키로 이동 (기존 데이터 호환)
        if old_norm in self.file_memos:
            self.file_memos[new_norm] = self.file_memos.pop(old_norm)
        elif old_path in self.file_memos:
            self.file_memos[new_norm] = self.file_memos.pop(old_path)
    
    def copy_memo(self, src_path, dest_path):
        """파일 복사 시 메모도 복사"""
        src_norm = self._norm(src_path)
        dest_norm = self._norm(dest_path)
        if src_norm in self.file_memos:
            self.file_memos[dest_norm] = self.file_memos[src_norm]
        elif src_path in self.file_memos:
            self.file_memos[dest_norm] = self.file_memos[src_path]
    
    def load_memos(self, memos_dict):
        """메모 데이터 로드"""
        self.file_memos = memos_dict.copy()
    
    def get_all_memos(self):
        """모든 메모 데이터 반환"""
        return self.file_memos.copy()
    
    def _on_directory_loaded(self, path):
        """디렉토리가 로드되면 해당 디렉토리의 모든 항목 데이터 갱신"""
        # 디렉토리 인덱스 가져오기
        dir_index = self.index(path)
        if not dir_index.isValid():
            return
        
        # 해당 디렉토리의 모든 자식 항목에 대해 dataChanged 시그널 발생
        row_count = self.rowCount(dir_index)
        if row_count > 0:
            first_index = self.index(0, 0, dir_index)
            last_index = self.index(row_count - 1, self.columnCount(dir_index) - 1, dir_index)
            self.dataChanged.emit(first_index, last_index)
    
    def refresh_file(self, file_path):
        """특정 파일의 정보를 강제로 갱신"""
        file_index = self.index(self._norm(file_path))
        if file_index.isValid():
            # 해당 파일의 모든 컬럼에 대해 dataChanged 시그널 발생
            last_column_index = self.index(file_index.row(), self.columnCount(file_index.parent()) - 1, file_index.parent())
            self.dataChanged.emit(file_index, last_column_index)
    
    def refresh_directory(self, dir_path):
        """특정 디렉토리의 모든 파일 정보를 강제로 갱신"""
        dir_index = self.index(dir_path)
        if not dir_index.isValid():
            return
        
        # 해당 디렉토리의 모든 자식 항목 갱신
        row_count = self.rowCount(dir_index)
        if row_count > 0:
            first_index = self.index(0, 0, dir_index)
            last_index = self.index(row_count - 1, self.columnCount(dir_index) - 1, dir_index)
            self.dataChanged.emit(first_index, last_index)


class FileSystemSortProxyModel(QSortFilterProxyModel):
    """폴더를 항상 위에 유지하는 커스텀 정렬 프록시 모델"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)
        self._filter_text = ""          # 현재 검색어
        self._root_path = ""            # 검색 기준 루트 경로

    def set_filter_text(self, text, root_path=""):
        """검색어와 루트 경로를 설정하고 필터를 갱신한다."""
        self._filter_text = text.strip().lower()
        self._root_path = os.path.normpath(root_path) if root_path else ""
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        """
        검색 필터 적용
        - 검색어 없음: 모든 행 표시 (기존 동작 유지)
        - 루트 직속 항목: 파일명에 검색어가 포함되면 표시
        - 하위 폴더: 내부에 매칭 자식이 있으면 폴더 자체도 표시
        """
        if not self._filter_text:
            return True

        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False

        file_path = source_model.filePath(index)
        file_name = source_model.fileName(index).lower()
        parent_path = os.path.normpath(os.path.dirname(file_path))

        if parent_path == self._root_path:
            # 루트 직속 항목: 이름에 검색어가 포함되면 표시
            if self._filter_text in file_name:
                return True
            # 폴더인 경우 하위에 매칭 항목이 있으면 표시
            if source_model.isDir(index):
                return self._has_matching_child(index)
            return False
        else:
            # 하위 폴더/파일: 검색어가 이름에 포함되거나 자식 중 매칭이 있으면 표시
            if self._filter_text in file_name:
                return True
            if source_model.isDir(index):
                return self._has_matching_child(index)
            return False

    def _has_matching_child(self, parent_index):
        """재귀적으로 하위 항목 중 검색어에 매칭되는 항목이 있는지 확인"""
        source_model = self.sourceModel()
        for row in range(source_model.rowCount(parent_index)):
            child = source_model.index(row, 0, parent_index)
            if not child.isValid():
                continue
            name = source_model.fileName(child).lower()
            if self._filter_text in name:
                return True
            if source_model.isDir(child) and self._has_matching_child(child):
                return True
        return False

    def lessThan(self, left, right):
        """정렬 비교 함수 - 폴더를 항상 위에 유지하면서 각 그룹 내에서 정렬"""
        source_model = self.sourceModel()
        
        # 왼쪽과 오른쪽 항목이 폴더인지 확인
        left_is_dir = source_model.isDir(left)
        right_is_dir = source_model.isDir(right)
        
        # 하나는 폴더고 하나는 파일인 경우
        # 폴더를 항상 위에 유지 (정렬 방향과 무관)
        if left_is_dir != right_is_dir:
            # 오름차순/내림차순에 관계없이 폴더가 항상 위에 오도록 함
            # 정렬 순서가 내림차순이어도 폴더가 항상 위에 오도록 처리
            if self.sortOrder() == Qt.DescendingOrder:
                return not left_is_dir  # 내림차순: False(파일) < True(폴더)로 반전
            else:
                return left_is_dir  # 오름차순: True(폴더) < False(파일)
        
        # 둘 다 폴더이거나 둘 다 파일인 경우, 컬럼에 따라 정렬
        column = left.column()
        
        if column == 0:  # Name
            left_data = source_model.fileName(left).lower()
            right_data = source_model.fileName(right).lower()
            return left_data < right_data
        elif column == 1:  # Size
            left_size = source_model.size(left)
            right_size = source_model.size(right)
            # 폴더의 경우 크기가 0이거나 의미가 없으므로 이름으로 정렬
            if left_is_dir and right_is_dir:
                left_data = source_model.fileName(left).lower()
                right_data = source_model.fileName(right).lower()
                return left_data < right_data
            return left_size < right_size
        elif column == 2:  # Type
            left_type = source_model.type(left).lower()
            right_type = source_model.type(right).lower()
            # Type이 같으면 이름으로 보조 정렬
            if left_type == right_type:
                left_name = source_model.fileName(left).lower()
                right_name = source_model.fileName(right).lower()
                return left_name < right_name
            return left_type < right_type
        elif column == 3:  # Date Modified
            left_date = source_model.lastModified(left)
            right_date = source_model.lastModified(right)
            # 날짜가 같으면 이름으로 보조 정렬
            if left_date == right_date:
                left_name = source_model.fileName(left).lower()
                right_name = source_model.fileName(right).lower()
                return left_name < right_name
            return left_date < right_date
        elif column == 4:  # Memo
            left_memo = source_model.data(left, Qt.DisplayRole) or ""
            right_memo = source_model.data(right, Qt.DisplayRole) or ""
            # 메모가 같으면 이름으로 보조 정렬
            if left_memo == right_memo:
                left_name = source_model.fileName(self.sourceModel().index(left.row(), 0, left.parent())).lower()
                right_name = source_model.fileName(self.sourceModel().index(right.row(), 0, right.parent())).lower()
                return left_name < right_name
            return left_memo.lower() < right_memo.lower()
        
        # 기본 정렬
        return super().lessThan(left, right)


class FilePropertiesDialog(QDialog):
    """파일 속성 대화상자"""
    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"속성 - {os.path.basename(file_path)}")
        self.resize(400, 300)
        
        layout = QVBoxLayout(self)
        
        info = QFileInfo(file_path)
        
        # 정보 표시
        details = QTextEdit()
        details.setReadOnly(True)
        
        text = f"""
<b>이름:</b> {info.fileName()}<br>
<b>경로:</b> {info.absolutePath()}<br>
<b>크기:</b> {self.format_size(info.size())}<br>
<b>생성일:</b> {info.birthTime().toString()}<br>
<b>수정일:</b> {info.lastModified().toString()}<br>
<b>읽기 전용:</b> {'예' if not info.isWritable() else '아니오'}<br>
<b>숨김:</b> {'예' if info.isHidden() else '아니오'}<br>
"""
        
        details.setHtml(text)
        layout.addWidget(details)
        
        # 닫기 버튼
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
    
    def format_size(self, size):
        """파일 크기 포맷"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0


# ====================================================================================
# 0. 유틸리티 함수 (Utility Functions)
# ====================================================================================
    # 프로그램 전역에서 사용되는 헬퍼 함수들입니다.
    # - create_blue_folder_icon: 커스텀 아이콘 생성
    # - format_size: 파일 크기 포맷팅

def create_blue_folder_icon(size: int = 256) -> QPixmap:
    """
    파란색 폴더 아이콘 생성
    [프로세스]
    1. 투명 배경의 QPixmap 생성
    2. 폴더 탭 영역 그리기
    3. 폴더 본체 영역 그리기
    4. 하이라이트 효과 추가
    5. 테두리 라인 그리기
    
    Args:
        size (int): 아이콘 크기 (픽셀, 최소 64)
    
    Returns:
        QPixmap: 생성된 폴더 아이콘
    """
    s = max(64, int(size))
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 폴더 디자인 비율
    margin = s * 0.08
    radius = s * 0.06
    body_top = s * 0.32
    tab_w = s * 0.50
    tab_h = s * 0.18

    # 폴더 탭과 본체 영역
    tab_rect = QRectF(margin, body_top - tab_h * 0.75, tab_w, tab_h)
    body_rect = QRectF(margin, body_top, s - 2 * margin, s - body_top - margin)

    # 색상 정의
    base = QColor(37, 99, 235)       # 파란색
    dark = QColor(30, 64, 175)       # 어두운 테두리
    light = QColor(96, 165, 250)     # 밝은 하이라이트

    # 폴더 탭 그리기
    path_tab = QPainterPath()
    path_tab.addRoundedRect(tab_rect, radius, radius)

    # 폴더 본체 그리기
    path_body = QPainterPath()
    path_body.addRoundedRect(body_rect, radius, radius)

    # 본체 채우기
    painter.setPen(Qt.NoPen)
    painter.setBrush(base)
    painter.drawPath(path_body)

    # 탭 채우기
    painter.setBrush(light)
    painter.drawPath(path_tab)

    # 하이라이트 효과 (위쪽 35%)
    highlight = QColor(light)
    highlight.setAlpha(140)
    painter.setBrush(highlight)
    painter.drawRoundedRect(
        QRectF(body_rect.x(), body_rect.y(), body_rect.width(), body_rect.height() * 0.35),
        radius,
        radius,
    )

    # 테두리 그리기
    outline = QPen(dark)
    outline.setWidthF(max(2.0, s * 0.02))
    painter.setPen(outline)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path_body)
    painter.drawPath(path_tab)

    painter.end()
    return pixmap


class TitleBar(QWidget):
    """
    커스텀 타이틀바 위젯
    [기능]
    1. 프레임리스 윈도우용 타이틀바 제공
    2. 아이콘, 제목, 최소화/최대화/닫기 버튼 포함
    3. 드래그로 윈도우 이동 기능
    4. 더블 클릭으로 최대화/복원
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # 참조 변수 및 드래그 상태 -----------------------------------------------
        self.parent_window = parent                                     # 부모 윈도우
        self._drag_pos = None                                           # 드래그 시작 위치

        # 위젯 설정 ---------------------------------------------------------------
        self.setObjectName('TitleBar')                                  # CSS 스타일링용 ID
        self.setFixedHeight(36)                                         # 타이틀바 높이

        # 레이아웃 구성 -----------------------------------------------------------
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(self.parent_window.windowTitle() if self.parent_window else "")
        self.title_label.setStyleSheet("font-weight:600; margin-left:6px;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # window control buttons
        self.min_btn = QPushButton("—")
        self.max_btn = QPushButton("☐")
        self.close_btn = QPushButton("✕")

        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.setFixedSize(28, 24)
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet("QPushButton{background:transparent; border:none; color:inherit;} QPushButton:hover{background:#3a3a3a;}" )

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        # connections
        self.min_btn.clicked.connect(self.on_minimize)
        self.max_btn.clicked.connect(self.on_maximize_restore)
        self.close_btn.clicked.connect(self.on_close)

    def setWindowIcon(self, pixmap: QPixmap):
        if pixmap:
            self.icon_label.setPixmap(pixmap.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def setWindowTitle(self, title: str):
        self.title_label.setText(title)

    def mouseDoubleClickEvent(self, event):
        self.on_maximize_restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            if self.parent_window.isMaximized():
                # when maximized, restore to normal and adjust position
                geom = self.parent_window.geometry()
                self.parent_window.showNormal()
                # set new position so cursor stays over title
                delta = event.globalPos() - self._drag_pos
                self.parent_window.move(self.parent_window.x() + delta.x(), self.parent_window.y() + delta.y())
                self._drag_pos = event.globalPos()
            else:
                delta = event.globalPos() - self._drag_pos
                self.parent_window.move(self.parent_window.pos() + delta)
                self._drag_pos = event.globalPos()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def on_minimize(self):
        if self.parent_window:
            self.parent_window.showMinimized()

    def on_maximize_restore(self):
        if not self.parent_window:
            return
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.max_btn.setText("☐")
        else:
            self.parent_window.showMaximized()
            self.max_btn.setText("❐")

    def on_close(self):
        if self.parent_window:
            self.parent_window.close()



class FileExplorer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Advanced File Explorer")
        self.resize(1600, 900)
        
        # 파란 폴더 아이콘 설정
        icon_pixmap = create_blue_folder_icon(256)
        self.setWindowIcon(QIcon(icon_pixmap))

        # 배율 조정 기능 (Ctrl + 마우스 휠)
        self.ui_scale = 1.0             # 기본 배율 (1.0 = 100%)
        self.min_scale = 0.7            # 최소 배율 (70%)
        self.max_scale = 2.0            # 최대 배율 (200%)
        self.scale_step = 0.1           # 한 번의 휠 이벤트당 변화량
        self.base_font_size = 9         # 기본 폰트 크기
        
        # 기본 테마(나중에 설정에서 변경 가능)
        self.apply_light_blue_theme()

        # 메뉴: 보기 -> 테마 추가
        self.create_view_menu()

        # 저장된 설정 로드 (테마, 컬럼 순서 등)
        self.load_settings()
        
        # 메모 저장 경로
        self.memo_file = self._config_dir() / "file_memos.json"
        
        # 공유 메모 딕셔너리 (모든 탭이 동일한 메모 데이터를 공유)
        self.shared_memos = {}
        
        # 클립보드 임시 저장
        self.clipboard_items = []
        self.clipboard_operation = None  # 'copy' or 'cut'
        
        # 네비게이션 히스토리는 탭별로 독립 관리 (tab.nav_history, tab.nav_history_index)
        # 하위 호환용 빈 딕셔너리 유지
        self.history = {'left': [], 'right': []}
        self.history_index = {'left': -1, 'right': -1}
        
        # 즐겨찾기 경로 매핑 (아이템 텍스트 -> 경로)
        self.favorite_paths = {}
        self.ignore_plus_activation = {'left': False, 'right': False}
        
        # 실행 취소 스택 (최근 작업 기록)
        self.undo_stack = []  # 각 항목: {'type': 'rename'/'move'/'copy', 'data': {...}}
        
        # 검색 워커 (검색 탭 제거 후에도 참조 오류 방지용)
        self.search_worker = None
        # UI 성능 최적화용 상태 변수
        self._last_expand_refresh = {}  # 폴더 경로별 마지막 강제 새로고침 시각
        self._size_workers = {}         # 비동기 파일 크기 계산 워커
        self._size_request_seq = 0      # 전역 파일 크기 요청 시퀀스
        

        
        # 파일 아이콘 프로바이더
        self.icon_provider = QFileIconProvider()
        
        # 메인 탭 위젯 (탐색기)
        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self.create_explorer_tab(), "📁 탐색기")
        
        # 상태바
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("준비")
        self.status_bar.addWidget(self.status_label)

        # Use native window frame (show OS title bar) and keep custom TitleBar hidden
        self.setWindowFlag(Qt.FramelessWindowHint, False)

        # create title bar and container
        self.title_bar = TitleBar(self)
        # apply app icon to title bar
        try:
            self.title_bar.setWindowIcon(icon_pixmap)
        except Exception:
            pass

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(self.title_bar)
        container_layout.addWidget(self.main_tabs)

        self.setCentralWidget(container)
        # default frameless state (native frame enabled)
        self.is_frameless = False
        # hide embedded custom title bar when using native OS frame
        try:
            if hasattr(self, 'title_bar'):
                self.title_bar.hide()
        except Exception:
            pass
        
        # 현재 활성 탭
        self.active_tab_widget = self.left_tabs
        self.active_side = 'left'

        # 저장된 탭 상태 로드 (없으면 기본 탭 생성)
        self.load_persisted_tabs()

        if not self._has_real_tab(self.left_tabs):
            default_left = "C:\\" if os.path.isdir("C:\\") else os.path.expanduser("~")
            self.add_tab(self.left_tabs, default_left, None, 'left')

        if not self._has_real_tab(self.right_tabs):
            default_right = "D:\\" if os.path.isdir("D:\\") else os.path.expanduser("~")
            self.add_tab(self.right_tabs, default_right, None, 'right')

        # + 탭 추가 (각 탭 위젯의 마지막에)
        self.add_plus_tab(self.left_tabs, 'left')
        self.add_plus_tab(self.right_tabs, 'right')
        
        # 단축키
        self.setup_shortcuts()
        
        # 메모 데이터 로드
        self.load_memos()
        
        # 주기적으로 현재 디렉토리 갱신 타이머 설정
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_current_directories)
        self.refresh_timer.start(10000)  # 10000ms = 10초
        # Re-apply the saved theme shortly after startup to override
        # any widget-level inline styles that were set during construction.
        try:
            saved = getattr(self, 'saved_theme_setting', None)
            if not saved:
                saved = getattr(self, 'current_theme', 'light')
            # small delay to ensure all widgets finished construction
            QTimer.singleShot(50, lambda: self.apply_theme(saved))
        except Exception:
            pass
        # 초기 활성 패널 하이라이트 적용 (짧은 지연 후)
        QTimer.singleShot(100, self.update_active_panel_highlight)
    
    def apply_light_blue_theme(self):
        """연한 파란-회색 테마 적용 (윈도우 스타일 유지)"""
        theme_stylesheet = """
        /* 메인 윈도우 배경 */
        QMainWindow {
            background-color: #f0f4f8;
        }
        
        /* 탭 위젯 */
        QTabWidget::pane {
            border: 1px solid #b8cfe0;
            background-color: #ffffff;
            border-radius: 4px;
        }

        /* 커스텀 타이틀바 */
        QWidget#TitleBar {
            background-color: #e8f0f7;
            border-bottom: 1px solid #b8cfe0;
        }
        
        QTabBar::tab {
            background-color: #dce8f0;
            color: #2c5282;
            padding: 6px 12px;
            margin-right: 2px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            border: 1px solid #b8cfe0;
            border-bottom: none;
            font-size: 9pt;
        }
        
        QTabBar::tab:selected {
            background-color: #ffffff;
            color: #1a365d;
            border-bottom: 2px solid #4a90e2;
        }
        
        QTabBar::tab:hover:!selected {
            background-color: #e6f0f7;
        }
        
        /* 메뉴바 */
        QMenuBar { background-color: #ffffff; color: #2c5282; }
        QMenuBar::item { background: transparent; padding: 6px 10px; }
        QMenuBar::item:selected { background-color: #e6f0f7; }
        
        /* 트리뷰 */
        QTreeView {
            background-color: #ffffff;
            alternate-background-color: #f7fafc;
            border: 1px solid #cbd5e0;
            border-radius: 4px;
            selection-background-color: #d6e9f7;
            selection-color: #1a365d;
            font-size: 9pt;
        }
        
        QTreeView::item:hover {
            background-color: #e6f0f7;
        }
        
        QTreeView::item:selected {
            background-color: #d6e9f7;
            color: #1a365d;
        }
        
        QTreeView::branch:has-children:!has-siblings:closed,
        QTreeView::branch:closed:has-children:has-siblings {
            border-image: none;
            image: none;
        }
        
        QTreeView::branch:open:has-children:!has-siblings,
        QTreeView::branch:open:has-children:has-siblings {
            border-image: none;
            image: none;
        }
        
        QTreeView::branch:has-children:closed {
            background: transparent;
        }
        
        QTreeView::branch:has-children:open {
            background: transparent;
        }
        
        /* 헤더 */
        QHeaderView::section {
            background-color: #e8f0f7;
            color: #2c5282;
            padding: 4px;
            border: 1px solid #b8cfe0;
            border-left: none;
            font-size: 9pt;
        }
        
        QHeaderView::section:hover {
            background-color: #d6e9f7;
        }
        
        /* 버튼 */
        QPushButton {
            background-color: #e8f0f7;
            color: #2c5282;
            border: 1px solid #b8cfe0;
            padding: 5px 10px;
            border-radius: 4px;
            font-size: 9pt;
        }
        
        QPushButton:hover {
            background-color: #d6e9f7;
            border-color: #4a90e2;
        }
        
        QPushButton:pressed {
            background-color: #c1daf2;
        }
        
        QPushButton:disabled {
            background-color: #e2e8f0;
            color: #a0aec0;
            border-color: #cbd5e0;
        }
        
        /* 입력 필드 */
        QLineEdit {
            background-color: #ffffff;
            color: #1a365d;
            border: 1px solid #b8cfe0;
            padding: 4px 8px;
            border-radius: 4px;
            selection-background-color: #d6e9f7;
            font-size: 9pt;
        }
        
        QLineEdit:focus {
            border: 2px solid #4a90e2;
        }
        
        QTextEdit {
            background-color: #ffffff;
            color: #2d3748;
            border: 1px solid #b8cfe0;
            border-radius: 4px;
            font-size: 9pt;
        }
        
        /* 리스트 위젯 */
        QListWidget {
            background-color: #ffffff;
            border: 1px solid #b8cfe0;
            border-radius: 4px;
            selection-background-color: #d6e9f7;
            selection-color: #1a365d;
            font-size: 9pt;
        }
        
        QListWidget::item:hover {
            background-color: #e6f0f7;
        }
        
        QListWidget::item:selected {
            background-color: #d6e9f7;
            color: #1a365d;
        }
        
        /* 테이블 위젯 */
        QTableWidget {
            background-color: #ffffff;
            alternate-background-color: #f7fafc;
            border: 1px solid #b8cfe0;
            border-radius: 4px;
            gridline-color: #e2e8f0;
            selection-background-color: #d6e9f7;
            selection-color: #1a365d;
            font-size: 9pt;
        }
        
        QTableWidget::item:hover {
            background-color: #e6f0f7;
        }
        
        /* 콤보박스 */
        QComboBox {
            background-color: #ffffff;
            color: #2c5282;
            border: 1px solid #b8cfe0;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 9pt;
        }
        
        QComboBox:hover {
            border-color: #4a90e2;
        }
        
        QComboBox::drop-down {
            border: none;
        }
        
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            border: 1px solid #b8cfe0;
            selection-background-color: #d6e9f7;
            selection-color: #1a365d;
            font-size: 9pt;
        }
        
        /* 스핀박스 */
        QSpinBox, QDateEdit {
            background-color: #ffffff;
            color: #2c5282;
            border: 1px solid #b8cfe0;
            padding: 3px 6px;
            border-radius: 4px;
            font-size: 9pt;
        }
        
        QSpinBox:focus, QDateEdit:focus {
            border: 2px solid #4a90e2;
        }
        
        /* 체크박스 */
        QCheckBox {
            color: #2c5282;
            spacing: 5px;
            font-size: 9pt;
        }
        
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border: 2px solid #b8cfe0;
            border-radius: 3px;
            background-color: #ffffff;
        }
        
        QCheckBox::indicator:checked {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }
        
        /* 라디오 버튼 */
        QRadioButton {
            color: #2c5282;
            spacing: 5px;
            font-size: 9pt;
        }
        
        QRadioButton::indicator {
            width: 16px;
            height: 16px;
            border: 2px solid #b8cfe0;
            border-radius: 8px;
            background-color: #ffffff;
        }
        
        QRadioButton::indicator:checked {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }
        
        /* 스크롤바 */
        QScrollBar:vertical {
            background-color: #f0f4f8;
            width: 14px;
            margin: 0px;
            border-radius: 7px;
        }
        
        QScrollBar::handle:vertical {
            background-color: #b8cfe0;
            min-height: 30px;
            border-radius: 7px;
        }
        
        QScrollBar::handle:vertical:hover {
            background-color: #4a90e2;
        }
        
        QScrollBar:horizontal {
            background-color: #f0f4f8;
            height: 14px;
            margin: 0px;
            border-radius: 7px;
        }
        
        QScrollBar::handle:horizontal {
            background-color: #b8cfe0;
            min-width: 30px;
            border-radius: 7px;
        }
        
        QScrollBar::handle:horizontal:hover {
            background-color: #4a90e2;
        }
        
        QScrollBar::add-line, QScrollBar::sub-line {
            border: none;
            background: none;
        }
        
        /* 스플리터 */
        QSplitter::handle {
            background-color: #cbd5e0;
        }
        
        QSplitter::handle:hover {
            background-color: #4a90e2;
        }
        
        /* 메뉴 */
        QMenu {
            background-color: #ffffff;
            border: 1px solid #b8cfe0;
            padding: 3px;
            font-size: 9pt;
        }
        
        QMenu::item {
            padding: 5px 25px 5px 18px;
            border-radius: 3px;
            color: #2c5282;
        }
        
        QMenu::item:selected {
            background-color: #d6e9f7;
        }
        
        QMenu::separator {
            height: 1px;
            background-color: #e2e8f0;
            margin: 3px 0px;
        }
        
        /* 상태바 */
        QStatusBar {
            background-color: #e8f0f7;
            color: #2c5282;
            border-top: 1px solid #b8cfe0;
            font-size: 9pt;
        }
        
        /* 그룹박스 */
        QGroupBox {
            border: 2px solid #b8cfe0;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
            color: #2c5282;
            font-size: 9pt;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
            color: #1a365d;
        }
        
        /* 다이얼로그 */
        QDialog {
            background-color: #f0f4f8;
        }
        
        /* 프로그레스바 */
        QProgressBar {
            border: 1px solid #b8cfe0;
            border-radius: 4px;
            text-align: center;
            background-color: #e8f0f7;
            color: #2c5282;
            font-size: 9pt;
        }
        
        QProgressBar::chunk {
            background-color: #4a90e2;
            border-radius: 3px;
        }
        
        /* 왼쪽 패널 헤더 (즐겨찾기 / 드라이브) */
        QLabel#LeftPanelHeader {
            font-weight: bold;
            padding: 5px 8px;
            color: #1a365d;
            background-color: #e8f0f7;
            border-radius: 4px;
            border: 1px solid #b8cfe0;
            font-size: 9pt;
        }

        /* 레이블 */
        QLabel {
            color: #2c5282;
            font-size: 9pt;
        }

        /* 주소창 — 라이트 테마 전용 강조 */
        QLineEdit#AddressBar {
            padding: 5px 10px;
            border: 1px solid #9abcd4;
            border-radius: 4px;
            background: #ffffff;
            font-size: 9pt;
            color: #1a365d;
            selection-background-color: #4a90e2;
            selection-color: #ffffff;
        }
        QLineEdit#AddressBar:focus {
            border: 2px solid #4a90e2;
            background: #f7fafc;
        }

        /* 트리뷰 내 검색창 — 라이트 테마 전용 강조 */
        QLineEdit#TreeSearchBar {
            padding: 4px 8px;
            border: 1px solid #9abcd4;
            border-radius: 4px;
            background: #f7fafc;
            font-size: 9pt;
            color: #1a365d;
            selection-background-color: #4a90e2;
            selection-color: #ffffff;
        }
        QLineEdit#TreeSearchBar:focus {
            border: 2px solid #4a90e2;
            background: #ffffff;
        }
        QLineEdit#TreeSearchBar:hover {
            border-color: #4a90e2;
        }
        """
        
        # Apply Fusion light palette for consistency
        try:
            QApplication.setStyle('Fusion')
            pal = QPalette()
            pal.setColor(QPalette.Window, QColor('#f0f4f8'))
            pal.setColor(QPalette.WindowText, QColor('#1a365d'))
            pal.setColor(QPalette.Base, QColor('#ffffff'))
            pal.setColor(QPalette.AlternateBase, QColor('#f7fafc'))
            pal.setColor(QPalette.ToolTipBase, QColor('#ffffff'))
            pal.setColor(QPalette.ToolTipText, QColor('#1a365d'))
            pal.setColor(QPalette.Text, QColor('#1a365d'))
            pal.setColor(QPalette.Button, QColor('#e8f0f7'))
            pal.setColor(QPalette.ButtonText, QColor('#2c5282'))
            pal.setColor(QPalette.Link, QColor('#4a90e2'))
            pal.setColor(QPalette.Highlight, QColor('#d6e9f7'))
            pal.setColor(QPalette.HighlightedText, QColor('#1a365d'))
            QApplication.setPalette(pal)
        except Exception:
            pass

        # UI 배율을 반영한 동적 폰트 크기 계산 및 적용
        base_size_9 = max(7, int(9 * self.ui_scale))
        base_size_8 = max(7, int(8 * self.ui_scale))
        
        # 스타일시트의 모든 폰트 크기를 동적으로 치환
        theme_stylesheet = theme_stylesheet.replace("font-size: 9pt;", f"font-size: {base_size_9}pt;")
        theme_stylesheet = theme_stylesheet.replace("font-size: 8pt;", f"font-size: {base_size_8}pt;")
        
        self.setStyleSheet(theme_stylesheet)

    def apply_dark_theme(self):
        """
        다크 그레이 테마 적용
        [프로세스]
        1. Qt Fusion 스타일 설정
        2. 팔레트 색상 정의 (어두운 회색 계열)
        3. 위젯별 상세 스타일시트 적용
        4. 호버/선택 효과 정의
        
        Note: 네이티브 윈도우 타이틀바는 OS가 제어합니다.
              전체 커스터마이징은 프레임리스 구현이 필요합니다.
        """
        # Set Fusion style and dark palette for consistent widget rendering
        try:
            QApplication.setStyle('Fusion')
        except Exception:
            pass

        palette = QPalette()
        # Base greys — 새 다크 테마 색상과 일치
        palette.setColor(QPalette.Window,          QColor('#16181f'))
        palette.setColor(QPalette.WindowText,      QColor('#d4e0f0'))
        palette.setColor(QPalette.Base,            QColor('#14161e'))
        palette.setColor(QPalette.AlternateBase,   QColor('#1c1f2b'))
        palette.setColor(QPalette.ToolTipBase,     QColor('#1e2230'))
        palette.setColor(QPalette.ToolTipText,     QColor('#d4e0f0'))
        palette.setColor(QPalette.Text,            QColor('#d4e0f0'))
        palette.setColor(QPalette.Button,          QColor('#252830'))
        palette.setColor(QPalette.ButtonText,      QColor('#c8d8ea'))
        palette.setColor(QPalette.BrightText,      QColor('#ffffff'))
        palette.setColor(QPalette.Link,            QColor('#5090d0'))
        palette.setColor(QPalette.Highlight,       QColor('#2a5090'))
        palette.setColor(QPalette.HighlightedText, QColor('#ffffff'))

        try:
            QApplication.setPalette(palette)
        except Exception:
            pass

        # Fine-tuned stylesheet for visual polish (dark-gray base)
        dark = """
        /* ── 메인 윈도우 ── */
        QMainWindow { background-color: #16181f; }

        /* ── 메뉴바 ── */
        QMenuBar { background-color: #1e2028; color: #c8d8ea; }
        QMenuBar::item { background: transparent; padding: 6px 10px; }
        QMenuBar::item:selected { background-color: #2a3040; color: #e8f0ff; }

        /* ── 탭 위젯 공통 (패널 하이라이트가 덮어쓰기 전 기본값) ── */
        QTabWidget::pane {
            border: 1px solid #2e3340;
            background-color: #14161e;
            border-radius: 4px;
        }

        /* ── 탭 바 ── */
        QTabBar::tab {
            background-color: #252830;
            color: #b0c4d8;
            padding: 6px 14px;
            margin-right: 3px;
            border: 1px solid #3a3f4a;
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            font-size: 9pt;
        }
        QTabBar::tab:selected {
            background-color: #14161e;
            color: #e8f0ff;
            border: 1px solid #4a90e2;
            border-bottom: 2px solid #14161e;
            font-weight: bold;
        }
        QTabBar::tab:hover:!selected {
            background-color: #2e3240;
            color: #c8ddf0;
        }

        /* ── 트리뷰 ── */
        QTreeView {
            background-color: #14161e;
            alternate-background-color: #1c1f2b;
            border: 1px solid #2e3340;
            border-radius: 4px;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
            color: #d4e0f0;
            font-size: 9pt;
            outline: none;
        }
        QTreeView::item {
            padding: 2px 0px;
            border-radius: 2px;
        }
        QTreeView::item:hover {
            background-color: #222840;
            color: #e8f0ff;
        }
        QTreeView::item:selected {
            background-color: #2a5090;
            color: #ffffff;
        }
        QTreeView::item:selected:hover {
            background-color: #3060aa;
            color: #ffffff;
        }
        QTreeView::branch:has-children:closed {
            background: transparent;
        }
        QTreeView::branch:has-children:open {
            background: transparent;
        }

        /* ── 헤더 ── */
        QHeaderView::section {
            background-color: #1e2230;
            color: #c0d4e8;
            border: 1px solid #3a4050;
            border-left: none;
            padding: 5px 4px;
            font-weight: bold;
            font-size: 9pt;
        }
        QHeaderView::section:first {
            border-left: 1px solid #3a4050;
        }
        QHeaderView::section:hover {
            background-color: #282e40;
            color: #e0eefc;
        }
        QHeaderView::section:pressed {
            background-color: #3a4a6a;
            color: #ffffff;
        }

        /* ── 버튼 ── */
        QPushButton {
            background-color: #252830;
            color: #c8d8ea;
            border: 1px solid #3a4050;
            padding: 5px 10px;
            border-radius: 4px;
            font-size: 9pt;
        }
        QPushButton:hover {
            background-color: #2e3448;
            color: #e8f0ff;
            border-color: #5a90c8;
        }
        QPushButton:pressed {
            background-color: #1a1e2a;
            color: #ffffff;
        }
        QPushButton:disabled {
            background-color: #1c1e25;
            color: #50607a;
            border-color: #2a2f3a;
        }

        /* ── 입력 필드 ── */
        QLineEdit {
            background-color: #1e2028;
            color: #d4e0f0;
            border: 1px solid #3a4050;
            padding: 4px 8px;
            border-radius: 4px;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
            font-size: 9pt;
        }
        QLineEdit:focus {
            border: 2px solid #4a90e2;
            background-color: #16181f;
            color: #e8f0ff;
        }
        QLineEdit:hover {
            border-color: #5080a8;
        }

        /* ── 텍스트 에디트 ── */
        QTextEdit {
            background-color: #14161e;
            color: #d4e0f0;
            border: 1px solid #2e3340;
            border-radius: 4px;
            font-size: 9pt;
        }

        /* ── 리스트 위젯 ── */
        QListWidget {
            background-color: #14161e;
            color: #d4e0f0;
            border: 1px solid #2e3340;
            border-radius: 4px;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
            font-size: 9pt;
        }
        QListWidget::item {
            padding: 3px 6px;
            border-radius: 2px;
        }
        QListWidget::item:hover {
            background-color: #222840;
            color: #e8f0ff;
        }
        QListWidget::item:selected {
            background-color: #2a5090;
            color: #ffffff;
        }

        /* ── 테이블 위젯 ── */
        QTableWidget {
            background-color: #14161e;
            alternate-background-color: #1c1f2b;
            color: #d4e0f0;
            gridline-color: #2e3340;
            border: 1px solid #2e3340;
            border-radius: 4px;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
            font-size: 9pt;
        }
        QTableWidget::item:hover {
            background-color: #222840;
        }

        /* ── 콤보박스 ── */
        QComboBox {
            background-color: #1e2028;
            color: #d4e0f0;
            border: 1px solid #3a4050;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 9pt;
        }
        QComboBox:hover { border-color: #5080a8; }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView {
            background-color: #1e2028;
            color: #d4e0f0;
            border: 1px solid #3a4050;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
        }

        /* ── 스핀박스 / 날짜 ── */
        QSpinBox, QDateEdit {
            background-color: #1e2028;
            color: #d4e0f0;
            border: 1px solid #3a4050;
            padding: 3px 6px;
            border-radius: 4px;
            font-size: 9pt;
        }
        QSpinBox:focus, QDateEdit:focus { border: 2px solid #4a90e2; }

        /* ── 체크박스 ── */
        QCheckBox { color: #c8d8ea; spacing: 5px; font-size: 9pt; }
        QCheckBox::indicator {
            width: 16px; height: 16px;
            border: 2px solid #3a4050;
            border-radius: 3px;
            background-color: #1e2028;
        }
        QCheckBox::indicator:checked {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }

        /* ── 라디오 버튼 ── */
        QRadioButton { color: #c8d8ea; spacing: 5px; font-size: 9pt; }
        QRadioButton::indicator {
            width: 16px; height: 16px;
            border: 2px solid #3a4050;
            border-radius: 8px;
            background-color: #1e2028;
        }
        QRadioButton::indicator:checked {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }

        /* ── 스크롤바 ── */
        QScrollBar:vertical {
            background-color: #1a1d25;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: #3a4a5a;
            min-height: 24px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #5090d0;
        }
        QScrollBar::handle:vertical:pressed {
            background-color: #6aace8;
        }
        QScrollBar:horizontal {
            background-color: #1a1d25;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: #3a4a5a;
            min-width: 24px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #5090d0;
        }
        QScrollBar::handle:horizontal:pressed {
            background-color: #6aace8;
        }
        QScrollBar::add-line, QScrollBar::sub-line { border: none; background: none; }

        /* ── 스플리터 ── */
        QSplitter::handle { background-color: #2e3340; }
        QSplitter::handle:hover { background-color: #4a90e2; }

        /* ── 메뉴 ── */
        QMenu {
            background-color: #1e2028;
            color: #d4e0f0;
            border: 1px solid #3a4050;
            padding: 3px;
            font-size: 9pt;
        }
        QMenu::item { padding: 5px 25px 5px 18px; border-radius: 3px; }
        QMenu::item:selected { background-color: #2a3a5a; color: #e8f0ff; }
        QMenu::separator { height: 1px; background-color: #2e3340; margin: 3px 0px; }

        /* ── 상태바 ── */
        QStatusBar {
            background-color: #1e2028;
            color: #a0b8cc;
            border-top: 1px solid #2e3340;
            font-size: 9pt;
        }

        /* ── 그룹박스 ── */
        QGroupBox {
            border: 1px solid #3a4050;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
            color: #c0d4e8;
            font-size: 9pt;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
            color: #a0c8e8;
        }

        /* ── 다이얼로그 ── */
        QDialog { background-color: #16181f; color: #d4e0f0; }

        /* ── 프로그레스바 ── */
        QProgressBar {
            background-color: #1a1d25;
            color: #c0d4e8;
            border: 1px solid #2e3340;
            border-radius: 4px;
            text-align: center;
            font-size: 9pt;
        }
        QProgressBar::chunk { background-color: #4a90e2; border-radius: 3px; }

        /* ── 왼쪽 패널 헤더 ── */
        QLabel#LeftPanelHeader {
            font-weight: bold;
            padding: 5px 8px;
            color: #a0c8e8;
            background-color: #1e2230;
            border-radius: 4px;
            border: 1px solid #3a4050;
            font-size: 9pt;
        }

        /* ── 일반 레이블 ── */
        QLabel { color: #c8d8ea; font-size: 9pt; }

        /* ── 커스텀 타이틀바 ── */
        QWidget#TitleBar {
            background-color: #1e2028;
            border-bottom: 1px solid #3a4050;
        }

        /* ── 주소창 — 다크 테마 전용 강조 ── */
        QLineEdit#AddressBar {
            padding: 5px 10px;
            background-color: #1a1d28;
            color: #d4e0f0;
            border: 1px solid #3a5070;
            border-radius: 4px;
            font-size: 9pt;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
        }
        QLineEdit#AddressBar:focus {
            border: 2px solid #5090d8;
            background-color: #161820;
        }
        QLineEdit#AddressBar:hover {
            border-color: #5080a8;
        }

        /* ── 트리뷰 내 검색창 — 다크 테마 전용 강조 ── */
        QLineEdit#TreeSearchBar {
            padding: 4px 8px;
            background-color: #1a1d28;
            color: #d4e0f0;
            border: 1px solid #3a5070;
            border-radius: 4px;
            font-size: 9pt;
            selection-background-color: #2a5090;
            selection-color: #ffffff;
        }
        QLineEdit#TreeSearchBar:focus {
            border: 2px solid #5090d8;
            background-color: #161820;
            color: #e8f0ff;
        }
        QLineEdit#TreeSearchBar:hover {
            border-color: #5080a8;
        }
        """
        
        # UI 배율을 반영한 동적 폰트 크기 계산 및 적용
        base_size_9 = max(7, int(9 * self.ui_scale))
        base_size_8 = max(7, int(8 * self.ui_scale))
        
        # 스타일시트의 모든 폰트 크기를 동적으로 치환
        dark = dark.replace("font-size: 9pt;", f"font-size: {base_size_9}pt;")
        dark = dark.replace("font-size: 8pt;", f"font-size: {base_size_8}pt;")
        
        self.setStyleSheet(dark)

    def detect_system_theme(self):
        """
        운영체제 테마 자동 감지
        [프로세스]
        1. 플랫폼 확인 (Windows, macOS, Linux)
        2. Windows: 레지스트리에서 테마 정보 조회
        3. 기타 OS: 기본값 'light' 반환
        
        Returns:
            str: 'light' 또는 'dark'
        """
        try:
            if platform.system() == 'Windows':
                try:
                    import winreg
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                    value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
                    winreg.CloseKey(key)
                    return 'light' if value == 1 else 'dark'
                except Exception:
                    return 'light'
            else:
                # macOS / Linux: 기본적으로 밝게 처리
                return 'light'
        except Exception:
            return 'light'

    def _settings_file(self) -> Path:
        return self._config_dir() / "settings.json"

    def load_settings(self):
        """설정 파일에서 테마와 컬럼 순서를 로드하고 적용한다."""
        sfile = self._settings_file()
        theme = 'light'
        column_order = None
        try:
            if sfile.exists():
                data = json.loads(sfile.read_text(encoding='utf-8'))
                theme = data.get('theme', 'light')
                column_order = data.get('column_order', None)
        except Exception:
            theme = 'light'
            column_order = None

        if theme == 'auto':
            theme = self.detect_system_theme()

        self.apply_theme(theme)
        # frameless titlebar is always enabled by design
        
        # 컬럼 순서 저장
        self.saved_column_order = column_order
    
    def load_theme_settings(self):
        """호환성을 위한 래퍼 메서드"""
        self.load_settings()

    def save_theme_settings(self, theme):
        sfile = self._settings_file()
        try:
            sfile.parent.mkdir(parents=True, exist_ok=True)
            # 기존 설정 로드
            payload = {'theme': theme}
            if sfile.exists():
                try:
                    existing_data = json.loads(sfile.read_text(encoding='utf-8'))
                    if isinstance(existing_data, dict):
                        payload = existing_data
                        payload['theme'] = theme
                except Exception:
                    pass
            sfile.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
    
    def save_column_order(self):
        """현재 활성 탭의 컬럼 순서를 저장"""
        sfile = self._settings_file()
        try:
            # 현재 활성 탭의 헤더에서 컬럼 순서 가져오기
            current_tab = self.active_tab_widget.currentWidget()
            if current_tab and hasattr(current_tab, 'tree'):
                header = current_tab.tree.header()
                column_count = header.count()
                column_order = [header.visualIndex(i) for i in range(column_count)]
                
                # 기존 설정 로드
                sfile.parent.mkdir(parents=True, exist_ok=True)
                payload = {}
                if sfile.exists():
                    try:
                        payload = json.loads(sfile.read_text(encoding='utf-8'))
                        if not isinstance(payload, dict):
                            payload = {}
                    except Exception:
                        pass
                
                # 컬럼 순서 저장
                payload['column_order'] = column_order
                sfile.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
                self.saved_column_order = column_order
        except Exception as e:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText(f"컬럼 순서 저장 실패: {e}")
    
    def restore_column_order(self, header):
        """저장된 컬럼 순서를 헤더에 복원"""
        if not hasattr(self, 'saved_column_order') or self.saved_column_order is None:
            return
        
        try:
            column_order = self.saved_column_order
            if not isinstance(column_order, list):
                return
            
            # 컬럼 수가 일치하는지 확인
            if len(column_order) != header.count():
                return
            
            # 컬럼 순서 복원
            for logical_index, visual_index in enumerate(column_order):
                header.moveSection(header.visualIndex(logical_index), visual_index)
        except Exception as e:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText(f"컬럼 순서 복원 실패: {e}")

    def apply_theme(self, theme_name: str):
        """Apply a theme. Accepts 'light', 'dark' or 'auto'. Tracks current and saved theme."""
        saved_setting = theme_name
        if theme_name == 'auto':
            theme_name = self.detect_system_theme()

        if theme_name == 'dark':
            self.apply_dark_theme()
        else:
            self.apply_light_blue_theme()

        # track currently applied theme and the saved user preference
        try:
            self.current_theme = 'dark' if theme_name == 'dark' else 'light'
            self.saved_theme_setting = saved_setting
        except Exception:
            pass
        # 메뉴 체크 상태 갱신 (있을 경우)
        try:
            if hasattr(self, 'theme_action_light') and hasattr(self, 'theme_action_dark') and hasattr(self, 'theme_action_auto'):
                if self.saved_theme_setting in ('light', 'dark'):
                    self.theme_action_auto.setChecked(False)
                    self.theme_action_light.setChecked(self.saved_theme_setting == 'light')
                    self.theme_action_dark.setChecked(self.saved_theme_setting == 'dark')
                else:
                    # saved was 'auto' — mark auto checked and reflect applied theme
                    self.theme_action_auto.setChecked(True)
                    self.theme_action_light.setChecked(self.current_theme == 'light')
                    self.theme_action_dark.setChecked(self.current_theme == 'dark')
        except Exception:
            pass
        # no frame_action to update; frameless titlebar is always used
        # 테마 변경 후 활성 패널 하이라이트 재적용
        try:
            if hasattr(self, 'left_tabs') and hasattr(self, 'right_tabs'):
                self.update_active_panel_highlight()
        except Exception:
            pass
    
    def create_view_menu(self):
        """메뉴바에 '보기 -> 테마' 추가"""
        menubar = self.menuBar()
        view_menu = menubar.addMenu("보기")

        theme_menu = QMenu("테마", self)

        # QActionGroup을 사용해 라디오처럼 동작하게 함
        group = QActionGroup(self)
        group.setExclusive(True)

        self.theme_action_light = QAction("라이트 테마", self, checkable=True)
        self.theme_action_dark = QAction("다크 테마", self, checkable=True)
        self.theme_action_auto = QAction("시스템 자동 감지", self, checkable=True)

        group.addAction(self.theme_action_light)
        group.addAction(self.theme_action_dark)
        group.addAction(self.theme_action_auto)

        # 연결: 선택 시 적용 및 저장
        self.theme_action_light.triggered.connect(lambda: (self.apply_theme('light'), self.save_theme_settings('light')))
        self.theme_action_dark.triggered.connect(lambda: (self.apply_theme('dark'), self.save_theme_settings('dark')))
        self.theme_action_auto.triggered.connect(lambda: (self.apply_theme(self.detect_system_theme()), self.save_theme_settings('auto')))

        theme_menu.addAction(self.theme_action_light)
        theme_menu.addAction(self.theme_action_dark)
        theme_menu.addSeparator()
        theme_menu.addAction(self.theme_action_auto)

        view_menu.addMenu(theme_menu)
        
        # 오른쪽 패널 표시/숨기기
        view_menu.addSeparator()
        self.toggle_right_panel_action = QAction("오른쪽 패널 표시", self, checkable=True)
        self.toggle_right_panel_action.setChecked(True)  # 기본값: 표시
        self.toggle_right_panel_action.setShortcut(QKeySequence("Ctrl+D"))
        self.toggle_right_panel_action.triggered.connect(self.toggle_right_panel)
        view_menu.addAction(self.toggle_right_panel_action)

        # ── 단축키 안내 메뉴 ─────────────────────────────────────────────
        menubar.addAction("⌨ 단축키").triggered.connect(self.show_shortcuts_dialog)

    # frameless toggle removed — frameless custom TitleBar is always used

    def wheelEvent(self, event):
        """
        마우스 휠 이벤트 처리
        [프로세스]
        1. Ctrl 키 확인
        2. 휠 각도(angleDelta)에 따라 배율 증감
        3. 배율을 min_scale ~ max_scale 범위 내로 제한
        4. apply_ui_scale() 호출하여 UI 업데이트
        """
        if event.modifiers() & Qt.ControlModifier:
            # 마우스 휠의 회전량 얻기 (y: 양수=위, 음수=아래)
            angle_delta = event.angleDelta().y()
            
            if angle_delta > 0:
                # 휠 위로: 확대
                self.ui_scale += self.scale_step
            elif angle_delta < 0:
                # 휠 아래: 축소
                self.ui_scale -= self.scale_step
            
            # 배율 범위 제한
            self.ui_scale = max(self.min_scale, min(self.max_scale, self.ui_scale))
            
            # UI 배율 적용
            self.apply_ui_scale()
            
            event.accept()
        else:
            super().wheelEvent(event)

    def apply_ui_scale(self):
        """
        전체 UI에 현재 배율을 적용
        [프로세스]
        1. 계산된 폰트 크기 = 기본 크기 * 배율
        2. QApplication 폰트 설정
        3. 트리뷰 등 주요 위젯의 높이 조정
        4. 상태바에 현재 배율 표시
        """
        # 1. 새로운 폰트 크기 계산
        scaled_font_size = max(7, int(self.base_font_size * self.ui_scale))
        
        # 2. QApplication 기본 폰트 설정
        app = QApplication.instance()
        app_font = app.font()
        app_font.setPointSize(scaled_font_size)
        app.setFont(app_font)
        
        # 3. 트리뷰의 행 높이 조정
        base_row_height = 20
        scaled_row_height = int(base_row_height * self.ui_scale)
        
        # 왼쪽 탭들의 트리뷰
        if hasattr(self, 'left_tabs'):
            for i in range(self.left_tabs.count()):
                tab_widget = self.left_tabs.widget(i)
                if tab_widget and hasattr(tab_widget, 'tree'):
                    tab_widget.tree.setUniformRowHeights(True)
                    if hasattr(tab_widget.tree, 'setRowHeight'):
                        # 각 행의 높이를 동적으로 설정하는 대신,
                        # 테마를 다시 적용하여 스타일시트 재계산
                        pass
        
        # 오른쪽 탭들의 트리뷰
        if hasattr(self, 'right_tabs'):
            for i in range(self.right_tabs.count()):
                tab_widget = self.right_tabs.widget(i)
                if tab_widget and hasattr(tab_widget, 'tree'):
                    tab_widget.tree.setUniformRowHeights(True)
        
        # 4. 상태바에 현재 배율 표시
        scale_percentage = int(self.ui_scale * 100)
        if hasattr(self, 'status_label'):
            self.status_label.setText(f"배율: {scale_percentage}%")
            # 2초 후 원래 상태 메시지로 복원
            QTimer.singleShot(2000, self.update_status_from_current_tab)
        
        # 5. 현재 테마 다시 적용하여 모든 스타일시트 반영
        try:
            current_theme_setting = getattr(self, 'saved_theme_setting', 'light')
            if current_theme_setting == 'dark':
                self.apply_dark_theme()
            else:
                self.apply_light_blue_theme()
        except Exception:
            pass

    def update_status_from_current_tab(self):
        """현재 활성 탭의 상태 메시지를 상태바에 표시"""
        try:
            current_tab = self.active_tab_widget.currentWidget()
            if current_tab and hasattr(current_tab, 'current_path'):
                path = current_tab.current_path
                self.status_label.setText(f"현재 위치: {path}")
            else:
                self.status_label.setText("준비")
        except Exception:
            self.status_label.setText("준비")

    def show_shortcuts_dialog(self):
        """현재 프로그램에서 사용 가능한 모든 단축키를 보여주는 다이얼로그"""
        dialog = QDialog(self)
        dialog.setWindowTitle("⌨  단축키 안내")
        dialog.resize(560, 560)  # 늘어난 항목을 위해 높이 약간 조정 (540 -> 560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Advanced File Explorer  —  단축키 목록")
        title.setStyleSheet("font-size: 11pt; font-weight: bold; padding-bottom: 4px;")
        layout.addWidget(title)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["단축키", "기능", "설명"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)

        rows = [
            # (카테고리 색, 단축키, 기능, 설명)
            ("nav",   "◀  뒤로가기 버튼",  "뒤로 가기",        "이전 방문 폴더로 이동"),
            ("nav",   "▶  앞으로가기 버튼", "앞으로 가기",       "다음 방문 폴더로 이동"),
            ("nav",   "⬆  위로가기 버튼",  "상위 폴더",         "현재 폴더의 부모 폴더로 이동"),
            ("nav",   "BackSpace",         "뒤로 가기",         "트리뷰 포커스 상태에서 이전 폴더로"),
            ("nav",   "Enter / 더블클릭",  "폴더/파일 열기",    "폴더 진입 또는 파일 실행 (다중 선택 지원)"),
            ("nav",   "Ctrl+D",            "패널 분할 토글",    "오른쪽 패널 표시/숨기기"),
            ("nav",   "Shift+우클릭",      "윈도우 탐색기 열기", "선택 항목 또는 현재 폴더를 네이티브 윈도우 탐색기로 열기"), # ✅ 추가된 부분
            ("file",  "Ctrl+C",            "복사",              "선택한 파일/폴더를 클립보드에 복사"),
            ("file",  "Ctrl+X",            "잘라내기",          "선택한 파일/폴더를 클립보드에 잘라내기"),
            ("file",  "Ctrl+V",            "붙여넣기",          "클립보드의 항목을 현재 폴더에 붙여넣기"),
            ("file",  "Delete",            "삭제",              "선택한 항목을 휴지통으로 이동"),
            ("file",  "F2",                "이름 바꾸기",       "선택한 항목의 이름 변경 (Name(F2) 컬럼)"),
            ("file",  "F3",                "메모 편집",         "선택한 항목의 메모 편집 (Memo(F3) 컬럼)"),
            ("file",  "Ctrl+Shift+N",      "새 폴더",           "현재 폴더에 새 폴더 생성"),
            ("file",  "Ctrl+Z",            "실행 취소",         "마지막 파일 작업(이름변경/이동/복사) 취소"),
            ("file",  "Ctrl+Shift+C",      "경로 복사",         "선택 항목의 전체 경로를 클립보드에 복사"),
            ("tab",   "Ctrl+T",            "새 탭",             "현재 패널에 새 탭 추가"),
            ("tab",   "Ctrl+W / × 버튼",  "탭 닫기",           "현재 탭 닫기 (마지막 탭은 닫히지 않음)"),
            ("view",  "F5",                "새로고침",          "현재 폴더 내용 갱신"),
            ("view",  "Ctrl+[ (왼쪽)",    "모두 접기",         "트리뷰의 모든 폴더를 일괄 접기"),
            ("view",  "Ctrl+] (오른쪽)",  "모두 펼치기",       "트리뷰의 모든 폴더를 일괄 펼치기"),
            ("view",  "Ctrl+마우스 휠 ↑↓", "배율 조정",        "GUI 배율 확대/축소 (70%~200%, 기본 100%)"),
        ]

        # 카테고리별 색상
        cat_colors = {
            "nav":  ("#e8f4fd", "#c5e3f7"),
            "file": ("#fef9e7", "#fdebd0"),
            "tab":  ("#eafaf1", "#d5f5e3"),
            "view": ("#f4ecf7", "#e8daef"),
        }

        table.setRowCount(len(rows))
        for r, (cat, key, func, desc) in enumerate(rows):
            light, dark = cat_colors.get(cat, ("#ffffff", "#f0f0f0"))
            bg = QColor(light if r % 2 == 0 else dark)

            key_item  = QTableWidgetItem(key)
            func_item = QTableWidgetItem(func)
            desc_item = QTableWidgetItem(desc)

            key_item.setFont(QFont("Consolas", 9))
            func_item.setFont(QFont("", 9, QFont.Bold))  # bold

            for item in (key_item, func_item, desc_item):
                item.setBackground(bg)
                item.setForeground(QColor("#1a365d"))

            table.setItem(r, 0, key_item)
            table.setItem(r, 1, func_item)
            table.setItem(r, 2, desc_item)

        layout.addWidget(table)

        close_btn = QPushButton("닫기")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(dialog.accept)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(close_btn)
        layout.addLayout(h)

        dialog.exec()

    def normalize_path(self, path):
        """경로를 Windows 형식(백슬래시)으로 정규화"""
        return path.replace("/", "\\")

    def _config_dir(self) -> Path:
        r"""설정/데이터 저장 폴더: 사용자 홈\.Advance_Explorer"""
        return Path.home() / ".Advance_Explorer"

    def _favorites_file(self) -> Path:
        return self._config_dir() / "favorites.json"

    def _tabs_file(self) -> Path:
        return self._config_dir() / "tabs.json"

    def _is_builtin_favorite_text(self, text: str) -> bool:
        return text.startswith(("🏠", "💼", "🖼️", "⬇️"))

    def load_persisted_favorites(self):
        """디스크에 저장된 즐겨찾기를 로드하여 목록에 반영"""
        # 폴더는 항상 만들어 둠 (사용자 홈\.Advance_Explorer)
        try:
            self._config_dir().mkdir(parents=True, exist_ok=True)
        except Exception:
            # 로드 자체는 계속 시도 (폴더 생성 실패 시 파일도 없을 가능성이 큼)
            pass

        favorites_file = self._favorites_file()
        if not favorites_file.exists():
            return

        try:
            data = json.loads(favorites_file.read_text(encoding="utf-8"))
        except Exception as e:
            if hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(f"즐겨찾기 로드 실패: {e}")
            return

        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return

        for entry in items:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            path = entry.get("path")
            if not isinstance(text, str) or not isinstance(path, str):
                continue
            if self._is_builtin_favorite_text(text):
                continue
            if text in self.favorite_paths:
                continue
            self.favorites.addItem(text)
            self.favorite_paths[text] = path

        if hasattr(self, "status_label") and self.status_label is not None:
            self.status_label.setText("즐겨찾기 로드 완료")

    def save_persisted_favorites(self):
        """현재 즐겨찾기(사용자 추가분) 목록을 디스크에 저장"""
        config_dir = self._config_dir()
        try:    
            config_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            if hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(f"설정 폴더 생성 실패: {e}")
            return

        items = []
        # QListWidget 순서를 그대로 저장
        for i in range(self.favorites.count()):
            item = self.favorites.item(i)
            if not item:
                continue
            text = item.text()
            if self._is_builtin_favorite_text(text):
                continue
            path = self.favorite_paths.get(text)
            if not path:
                continue
            items.append({"text": text, "path": path})

        payload = {
            "version": 1,
            "items": items,
        }

        try:
            self._favorites_file().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            if hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(f"즐겨찾기 저장 실패: {e}")
            return

    def _has_real_tab(self, tab_widget):
        """+ 탭을 제외한 실제 탭이 있는지 확인"""
        for i in range(tab_widget.count()):
            widget = tab_widget.widget(i)
            if widget and not (hasattr(widget, 'is_plus_tab') and widget.is_plus_tab):
                return True
        return False

    def save_persisted_tabs(self):
        """현재 열려있는 트리뷰 탭 정보를 디스크에 저장"""
        config_dir = self._config_dir()
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            if hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(f"탭 저장 폴더 생성 실패: {e}")
            return

        def collect(tab_widget):
            paths = []
            real_indices = []
            for i in range(tab_widget.count()):
                widget = tab_widget.widget(i)
                if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                    continue
                if hasattr(widget, 'current_path'):
                    paths.append(widget.current_path)
                    real_indices.append(i)
            current_index = tab_widget.currentIndex()
            current_real_index = 0
            if real_indices and current_index in real_indices:
                current_real_index = real_indices.index(current_index)
            return paths, current_real_index

        left_paths, left_current = collect(self.left_tabs)
        right_paths, right_current = collect(self.right_tabs)

        payload = {
            "version": 1,
            "left": {"paths": left_paths, "current_index": left_current},
            "right": {"paths": right_paths, "current_index": right_current},
            "active_side": self.active_side,
        }

        try:
            self._tabs_file().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            if hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(f"탭 저장 실패: {e}")

    def load_persisted_tabs(self):
        """디스크에 저장된 트리뷰 탭 정보를 로드"""
        tabs_file = self._tabs_file()
        if not tabs_file.exists():
            return False

        try:
            data = json.loads(tabs_file.read_text(encoding="utf-8"))
        except Exception:
            return False

        def load_side(tab_widget, side_key):
            info = data.get(side_key, {}) if isinstance(data, dict) else {}
            paths = info.get("paths", []) if isinstance(info, dict) else []
            current_index = info.get("current_index", 0) if isinstance(info, dict) else 0

            for p in paths:
                if isinstance(p, str) and os.path.isdir(p):
                    self.add_tab(tab_widget, p, None, side_key)

            real_indices = [i for i in range(tab_widget.count())
                            if not (hasattr(tab_widget.widget(i), 'is_plus_tab') and tab_widget.widget(i).is_plus_tab)]
            if real_indices:
                idx = max(0, min(current_index, len(real_indices) - 1))
                tab_widget.setCurrentIndex(real_indices[idx])
                return True
            return False

        left_loaded = load_side(self.left_tabs, 'left')
        right_loaded = load_side(self.right_tabs, 'right')

        saved_active = data.get("active_side") if isinstance(data, dict) else None
        if saved_active in ('left', 'right'):
            self.active_side = saved_active
            self.active_tab_widget = self.left_tabs if saved_active == 'left' else self.right_tabs

        return left_loaded or right_loaded
    
    def create_search_tab(self):
        """
        검색 탭 UI 생성
        [프로세스]
        1. 인덱스 상태 패널 구성
        2. 검색 옵션 패널 구성 (파일명, 내용, 크기, 날짜 필터)
        3. 검색 결과 테이블 구성
        4. 검색 버튼 및 이벤트 연결
        """
        search_widget = QWidget()
        main_layout = QVBoxLayout(search_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 인덱싱 상태 패널
        index_status_group = QGroupBox("📊 인덱스 상태")
        index_status_layout = QVBoxLayout()
        
        status_info_layout = QHBoxLayout()
        self.index_status_label = QLabel("인덱스: 초기화 중...")
        self.index_status_label.setStyleSheet("font-weight: bold;")
        status_info_layout.addWidget(self.index_status_label)
        status_info_layout.addStretch()
        
        self.rebuild_index_btn = QPushButton("🔄 인덱스 재구축")
        self.rebuild_index_btn.clicked.connect(self.rebuild_index)
        self.rebuild_index_btn.setEnabled(False)
        status_info_layout.addWidget(self.rebuild_index_btn)
        
        self.use_index_check = QCheckBox("빠른 검색 사용 (인덱스)")
        self.use_index_check.setChecked(True)
        self.use_index_check.setToolTip("인덱스를 사용하여 초고속 검색 (인덱싱된 파일만 검색)")
        status_info_layout.addWidget(self.use_index_check)
        
        index_status_layout.addLayout(status_info_layout)
        
        # 인덱싱 진행률
        self.indexing_progress_label = QLabel("")
        self.indexing_progress_label.setStyleSheet("padding: 3px; color: #666;")
        index_status_layout.addWidget(self.indexing_progress_label)
        
        index_status_group.setLayout(index_status_layout)
        main_layout.addWidget(index_status_group)
        
        # 검색 입력 영역
        search_input_group = QGroupBox("🔍 검색 조건")
        search_input_layout = QVBoxLayout()
        
        # 검색어 입력
        query_layout = QHBoxLayout()
        query_layout.addWidget(QLabel("검색어:"))
        self.search_query_input = QLineEdit()
        self.search_query_input.setPlaceholderText("검색할 파일명 또는 내용 입력...")
        self.search_query_input.returnPressed.connect(self.start_search)
        query_layout.addWidget(self.search_query_input)
        search_input_layout.addLayout(query_layout)
        
        # 검색 위치 (드라이브 선택)
        drive_layout = QHBoxLayout()
        drive_layout.addWidget(QLabel("검색 위치:"))
        
        self.drive_checkboxes = []
        
        if platform.system() == 'Windows':
            # Windows: 사용 가능한 드라이브 검색
            import string
            from ctypes import windll
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drive_path = f"{letter}:\\"
                    if os.path.exists(drive_path):
                        checkbox = QCheckBox(f"{letter}:")
                        # C 드라이브는 기본 선택
                        if letter == 'C':
                            checkbox.setChecked(True)
                        checkbox.setProperty("drive_path", drive_path)
                        self.drive_checkboxes.append(checkbox)
                        drive_layout.addWidget(checkbox)
                bitmask >>= 1
        else:
            # Linux/Mac: 루트 디렉토리와 홈
            root_checkbox = QCheckBox("전체(/)")
            root_checkbox.setProperty("drive_path", "/")
            self.drive_checkboxes.append(root_checkbox)
            drive_layout.addWidget(root_checkbox)
            
            home_checkbox = QCheckBox("홈")
            home_checkbox.setChecked(True)
            home_checkbox.setProperty("drive_path", os.path.expanduser("~"))
            self.drive_checkboxes.append(home_checkbox)
            drive_layout.addWidget(home_checkbox)
        
        drive_layout.addStretch()
        search_input_layout.addLayout(drive_layout)
        
        # 파일 형식 필터
        file_type_layout = QHBoxLayout()
        file_type_layout.addWidget(QLabel("파일 형식:"))
        self.file_type_combo = QComboBox()
        self.file_type_combo.addItems([
            "모든 파일",
            "txt", "py", "js", "html", "css", "json", "xml",
            "jpg", "png", "gif", "bmp", "svg",
            "pdf", "doc", "docx", "xls", "xlsx",
            "mp3", "mp4", "avi", "mkv",
            "zip", "rar", "7z"
        ])
        self.file_type_combo.setEditable(True)
        file_type_layout.addWidget(self.file_type_combo)
        file_type_layout.addStretch()
        search_input_layout.addLayout(file_type_layout)
        
        # 검색 옵션
        options_layout = QHBoxLayout()
        self.case_insensitive_check = QCheckBox("대소문자 구분 안함")
        self.case_insensitive_check.setChecked(True)
        self.case_insensitive_check.setToolTip("대소문자를 구분하지 않고 검색")
        options_layout.addWidget(self.case_insensitive_check)
        
        self.flexible_word_check = QCheckBox("단어 순서 무시")
        self.flexible_word_check.setToolTip("검색어의 단어들이 순서에 관계없이 파일명에 포함되면 매칭\n예: 'Auto Hwang' → 'Trader_Hwang_Auto.txt' 검색 가능")
        options_layout.addWidget(self.flexible_word_check)
        
        options_layout.addStretch()
        search_input_layout.addLayout(options_layout)
        
        search_input_group.setLayout(search_input_layout)
        main_layout.addWidget(search_input_group)
        
        # 검색 버튼
        button_layout = QHBoxLayout()
        self.search_start_btn = QPushButton("🔍 검색 시작")
        self.search_start_btn.setStyleSheet("QPushButton { padding: 8px 20px; font-size: 10pt; font-weight: bold; background-color: #4a90e2; color: white; border: none; } QPushButton:hover { background-color: #357abd; }")
        self.search_start_btn.clicked.connect(self.start_search)
        button_layout.addWidget(self.search_start_btn)
        
        self.search_stop_btn = QPushButton("⏹️ 중지")
        self.search_stop_btn.setStyleSheet("QPushButton { padding: 8px 20px; font-size: 10pt; }")
        self.search_stop_btn.setEnabled(False)
        self.search_stop_btn.clicked.connect(self.stop_search)
        button_layout.addWidget(self.search_stop_btn)
        
        self.search_clear_btn = QPushButton("🗑️ 결과 지우기")
        self.search_clear_btn.setStyleSheet("QPushButton { padding: 6px 14px; font-size: 9pt; }")
        self.search_clear_btn.clicked.connect(self.clear_search_results)
        button_layout.addWidget(self.search_clear_btn)
        
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        
        # 진행 상황 표시
        self.search_progress_label = QLabel("대기 중...")
        self.search_progress_label.setStyleSheet("padding: 5px 8px; background: #e8f0f7; border: 1px solid #b8cfe0; border-radius: 4px; color: #2c5282; font-size: 9pt;")
        main_layout.addWidget(self.search_progress_label)
        
        # 검색 결과 테이블
        result_label = QLabel("📊 검색 결과")
        result_label.setStyleSheet("font-weight: bold; font-size: 10pt; padding: 5px; color: #1a365d;")
        main_layout.addWidget(result_label)
        
        self.search_results_table = QTableWidget()
        self.search_results_table.setColumnCount(5)
        self.search_results_table.setHorizontalHeaderLabels(["파일명", "경로", "크기", "수정 날짜", "매칭 유형"])
        
        # 컬럼 너비 자동 조정
        header = self.search_results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        
        # 더블클릭으로 파일 열기
        self.search_results_table.doubleClicked.connect(self.open_search_result)
        
        # 컨텍스트 메뉴
        self.search_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.search_results_table.customContextMenuRequested.connect(self.show_search_result_context_menu)
        
        main_layout.addWidget(self.search_results_table)
        
        # 검색 워커 초기화
        self.search_worker = None
        
        return search_widget
        
    def create_explorer_tab(self):
        """
        탐색기 탭 UI 생성
        [프로세스]
        1. 좌측 패널 생성 (즈겨찾기, 드라이브 목록)
        2. 중앙/우측 패널 생성 (듀얼 탭 위젯)
        3. 스플리터로 패널 레이아웃 구성
        4. 초기 크기 설정 및 반환
        """
        explorer_widget = QWidget()
        explorer_layout = QVBoxLayout(explorer_widget)
        explorer_layout.setContentsMargins(0, 0, 0, 0)
        explorer_layout.setSpacing(0)
        
        # 메인 Splitter
        main_splitter = QSplitter(Qt.Horizontal)
        
        # 왼쪽 패널 (즐겨찾기 + 드라이브)
        left_panel = self.create_left_panel()
        main_splitter.addWidget(left_panel)
        
        # 중앙 및 오른쪽 패널
        self.tabs_splitter = QSplitter(Qt.Horizontal)
        
        # 왼쪽 탭
        self.left_tabs = self.create_tab_widget('left')
        self.tabs_splitter.addWidget(self.left_tabs)
        
        # 오른쪽 탭
        self.right_tabs = self.create_tab_widget('right')
        self.tabs_splitter.addWidget(self.right_tabs)
        
        self.tabs_splitter.setSizes([800, 800])
        main_splitter.addWidget(self.tabs_splitter)
        
        main_splitter.setSizes([200, 1400])
        
        explorer_layout.addWidget(main_splitter)
        
        return explorer_widget
        
    def create_left_panel(self):
        """왼쪽 패널 (즐겨찾기 + 드라이브)"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 즐겨찾기 섹션
        fav_label = QLabel("⭐ 즐겨찾기")
        fav_label.setObjectName("LeftPanelHeader")
        layout.addWidget(fav_label)
        
        self.favorites = QListWidget()
        # 즐겨찾기 드래그앤드롭 재정렬 허용
        self.favorites.setDragEnabled(True)
        self.favorites.setAcceptDrops(True)
        self.favorites.setDropIndicatorShown(True)
        self.favorites.setDragDropMode(QAbstractItemView.InternalMove)
        self.favorites.setDefaultDropAction(Qt.MoveAction)
        # 순서 변경 시 저장
        self.favorites.model().rowsMoved.connect(lambda *args: self.save_persisted_favorites())
        
        # 기본 즐겨찾기 추가 (경로 매핑과 함께)
        home_path = os.path.expanduser("~")
        docs_path = os.path.join(home_path, "Documents")
        pics_path = os.path.join(home_path, "Pictures")
        down_path = os.path.join(home_path, "Downloads")
        
        self.favorites.addItem("🏠 홈")
        self.favorite_paths["🏠 홈"] = home_path
        
        self.favorites.addItem("💼 문서")
        self.favorite_paths["💼 문서"] = docs_path
        
        self.favorites.addItem("🖼️ 사진")
        self.favorite_paths["🖼️ 사진"] = pics_path
        
        self.favorites.addItem("⬇️ 다운로드")
        self.favorite_paths["⬇️ 다운로드"] = down_path
        
        self.favorites.itemDoubleClicked.connect(self.on_favorite_clicked)
        self.favorites.setContextMenuPolicy(Qt.CustomContextMenu)
        self.favorites.customContextMenuRequested.connect(self.show_favorites_context_menu)
        
        # 즐겨찾기에서 Ctrl+Shift+C 단축키 지원
        self.favorites.installEventFilter(self)

        # 저장된 즐겨찾기(사용자 추가분) 로드
        self.load_persisted_favorites()
        layout.addWidget(self.favorites)
        
        # 드라이브 섹션
        drive_label = QLabel("💾 드라이브")
        drive_label.setObjectName("LeftPanelHeader")
        layout.addWidget(drive_label)
        
        self.drives = QListWidget()
        self.load_drives()
        self.drives.itemDoubleClicked.connect(self.on_drive_clicked)
        layout.addWidget(self.drives)
        
        return panel
    
    def load_drives(self):
        """드라이브 목록 로드"""
        self.drives.clear()
        if platform.system() == 'Windows':
            import string
            from ctypes import windll
            drives = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(f"{letter}:\\")
                bitmask >>= 1
            
            for drive in drives:
                self.drives.addItem(f"💿 {drive}")
        else:
            self.drives.addItem("💿 /")
    
    def create_tab_widget(self, side):
        """탭 위젯 생성"""
        tabs = DraggableTabWidget()
        tabs.setTabsClosable(True)
        tabs.tabCloseRequested.connect(lambda idx: self.close_tab(tabs, idx, side))
        tabs.parent_window = self  # 부모 윈도우 참조 설정
        tabs.side = side  # 어느 쪽인지 설정
        
        # 탭 바에 컨텍스트 메뉴 추가
        tab_bar = tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(lambda pos: self.show_tab_context_menu(tabs, pos, side))
        # 탭 바 빈 공간 클릭도 활성 사이드로 인식하기 위해 이벤트 필터 설치
        tab_bar.setProperty("tab_side", side)
        tab_bar.installEventFilter(self)
        
        if side == 'left':
            tabs.currentChanged.connect(self.on_left_tab_changed)
        else:
            tabs.currentChanged.connect(self.on_right_tab_changed)
        
        return tabs
    
    def add_tab(self, tab_widget, path, name, side):
        """
        새로운 탐색기 탭 추가
        [프로세스]
        1. 탭 이름 자동 생성 (경로를 기반으로)
        2. 탭 내부 UI 구성 (주소창, 네비게이션 버튼)
        3. 파일 트리뷰 및 모델 설정
        4. 저장된 컬럼 순서 복원
        5. 이벤트 핸들러 연결 및 히스토리 초기화
        """
        # 이름이 지정되지 않은 경우 자동 생성 (폴더 이름 사용, 길면 줄임)
        if name is None:
            raw_name = os.path.basename(path) or path
            name = self._tab_display_name(raw_name)
        
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # 주소창 레이아웃
        address_layout = QHBoxLayout()
        
        # 뒤로 버튼
        back_btn = QPushButton("◀")
        back_btn.setFixedSize(32, 32)
        back_btn.setToolTip("뒤로 (이전 방문 폴더)")
        back_btn.setEnabled(False)  # 초기에는 비활성
        back_btn.clicked.connect(lambda: self.navigate_back(tab, side))
        address_layout.addWidget(back_btn)
        
        # 앞으로 버튼
        forward_btn = QPushButton("▶")
        forward_btn.setFixedSize(32, 32)
        forward_btn.setToolTip("앞으로 (다음 방문 폴더)")
        forward_btn.setEnabled(False)  # 초기에는 비활성
        forward_btn.clicked.connect(lambda: self.navigate_forward(tab, side))
        address_layout.addWidget(forward_btn)
        
        # 상위 폴더 버튼
        up_btn = QPushButton("⬆")
        up_btn.setFixedSize(32, 32)
        up_btn.setToolTip("상위 폴더 (부모 폴더로 이동)")
        up_btn.clicked.connect(lambda: self.go_up(tab, side))
        address_layout.addWidget(up_btn)
        
        # 주소창 (커스텀 AddressBar 사용)
        address_bar = AddressBar()
        address_bar.setText(self.normalize_path(path))
        # 인라인 스타일 없이 전역 테마 스타일시트에 위임
        # (라이트/다크 테마 전환 시 자동으로 올바른 색상 적용됨)
        address_bar.setObjectName("AddressBar")
        address_bar.returnPressed.connect(lambda: self.on_address_changed(tab, side))
        address_layout.addWidget(address_bar)
        
        # 분할 보기 토글 버튼 (왼쪽 탭에만)
        if side == 'left':
            toggle_split_btn = QPushButton("⚡")
            toggle_split_btn.setFixedSize(32, 32)
            toggle_split_btn.setToolTip("오른쪽 패널 표시/숨기기 (Ctrl+D)")
            toggle_split_btn.setCheckable(True)
            toggle_split_btn.setChecked(True)  # 기본값: 표시
            toggle_split_btn.clicked.connect(self.toggle_right_panel)
            address_layout.addWidget(toggle_split_btn)
            # 버튼 참조 저장 (나중에 상태 업데이트용)
            self.toggle_split_btn = toggle_split_btn
        
        # 되돌아가기 버튼
        undo_btn = QPushButton("↩️")
        undo_btn.setFixedSize(32, 32)
        undo_btn.setToolTip("되돌아가기 (Ctrl+Z)")
        undo_btn.clicked.connect(self.undo_last_operation)
        address_layout.addWidget(undo_btn)
        
        # 새로고침 버튼 (애니메이션 버전)
        refresh_btn = AnimatedRefreshButton("🔄")
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setToolTip("새로고침 (F5)")
        refresh_btn.clicked.connect(lambda: self.refresh_view_with_animation(tab, refresh_btn))
        address_layout.addWidget(refresh_btn)

        layout.addLayout(address_layout)

        # ── 툴바 2행: 모두 접기/펼치기 버튼 + 검색창 ────────────────────────
        toolbar2_layout = QHBoxLayout()
        toolbar2_layout.setSpacing(4)

        # 모두 접기 버튼
        collapse_btn = QPushButton("⊟")
        collapse_btn.setFixedSize(32, 32)
        collapse_btn.setToolTip("모두 접기 (Ctrl+[)")
        collapse_btn.clicked.connect(lambda: self.collapse_all(tab))
        toolbar2_layout.addWidget(collapse_btn)

        # 모두 펼치기 버튼
        expand_btn = QPushButton("⊞")
        expand_btn.setFixedSize(32, 32)
        expand_btn.setToolTip("모두 펼치기 (Ctrl+])")
        expand_btn.clicked.connect(lambda: self.expand_all(tab))
        toolbar2_layout.addWidget(expand_btn)

        # 구분선
        sep_label = QLabel("|")
        sep_label.setFixedWidth(10)
        sep_label.setAlignment(Qt.AlignCenter)
        sep_label.setStyleSheet("color: #506070; font-weight: bold; font-size: 11pt;")
        toolbar2_layout.addWidget(sep_label)

        # 검색 아이콘
        search_icon_label = QLabel("🔍")
        search_icon_label.setStyleSheet("font-size: 11pt; padding: 0 2px;")
        toolbar2_layout.addWidget(search_icon_label)

        # 검색 입력창 — objectName으로 전역 테마 스타일 적용 + 추가 스타일
        search_bar = QLineEdit()
        search_bar.setObjectName("TreeSearchBar")
        search_bar.setPlaceholderText("파일/폴더 이름으로 검색... (ESC: 초기화)")
        search_bar.setClearButtonEnabled(True)
        search_bar.setToolTip("현재 열린 폴더 안의 파일·폴더 이름으로 검색 (ESC로 초기화)")
        search_bar.textChanged.connect(lambda text, t=tab: self._on_search_changed(t, text))
        toolbar2_layout.addWidget(search_bar)

        layout.addLayout(toolbar2_layout)

        # 파일 뷰 (기본 트리뷰) - 커스텀 TreeView 사용
        tree = CustomTreeView()
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree.setDragEnabled(True)
        tree.setAcceptDrops(True)
        tree.setDropIndicatorShown(True)
        tree.setDragDropMode(QAbstractItemView.DragDrop)
        # 더블클릭·클릭으로 편집 모드가 열리지 않도록 설정
        # (F2/F3 단축키에서만 명시적으로 tree.edit() 호출)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 키보드 단축키 연결
        tree.navigate_back_callback = lambda: self.navigate_back(tab, side)
        tree.open_item_callback = lambda idx: self.on_tree_item_activated(tab, idx, side)
        tree.drop_callback = lambda mime_data, drop_idx, is_copy: self.handle_drop(tab, mime_data, drop_idx, is_copy)
        tree.side = side
        tree.parent_window = self  # 부모 윈도우 참조 저장
        
        # 컨텍스트 메뉴
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self.show_context_menu(tree, pos, tab))
        
        layout.addWidget(tree)
        
        # 상태 정보
        status_layout = QHBoxLayout()
        item_count_label = QLabel("0 개 항목")
        status_layout.addWidget(item_count_label)
        status_layout.addStretch()
        selected_label = QLabel("")
        status_layout.addWidget(selected_label)
        layout.addLayout(status_layout)
        
        # 파일 시스템 모델 (공유 메모 딕셔너리와 콜백 전달)
        source_model = FileSystemModel(
            shared_memos=self.shared_memos,
            memo_changed_callback=self.on_memo_changed,
            rename_done_callback=self._on_inline_rename_done
        )
        source_model.setRootPath("")
        
        # 정렬 프록시 모델 (폴더를 항상 위에 유지)
        proxy_model = FileSystemSortProxyModel()
        proxy_model.setSourceModel(source_model)
        
        tree.setModel(proxy_model)
        tree.setRootIndex(proxy_model.mapFromSource(source_model.index(path)))
        
        # 정렬 활성화
        tree.setSortingEnabled(True)
        tree.sortByColumn(0, Qt.AscendingOrder)  # 기본: 이름으로 오름차순
        
        # 헤더 클릭으로 정렬 가능하도록 설정
        header = tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionsMovable(True)  # 컬럼 이동 가능하도록 설정
        
        # 컬럼 너비 조정
        tree.setColumnWidth(0, 250)  # Name
        tree.setColumnWidth(1, 100)  # Size
        tree.setColumnWidth(2, 120)  # Type
        tree.setColumnWidth(3, 150)  # Date Modified
        tree.setColumnWidth(4, 300)  # Memo (더 넓게 설정)
        
        # Name 컬럼에 커스텀 델리게이트 적용 (F2 편집 시 텍스트가 잘리지 않도록)
        name_delegate = NameItemDelegate(tree, main_window=self)
        tree.setItemDelegateForColumn(0, name_delegate)

        # 메모 컬럼에 커스텀 델리게이트 적용 (메인 윈도우 참조 전달)
        memo_delegate = MemoItemDelegate(tree, main_window=self)
        tree.setItemDelegateForColumn(4, memo_delegate)
        
        # 저장된 컬럼 순서 복원
        self.restore_column_order(header)
        
        # 컬럼 이동 시 저장
        header.sectionMoved.connect(lambda: self.save_column_order())
        
        # 탭 추가
        tab_widget.addTab(tab, name)
        
        # 속성 저장
        tab.tree = tree
        tab.model = proxy_model  # 프록시 모델 저장
        tab.source_model = source_model  # 원본 모델도 저장
        tab.address_bar = address_bar
        tab.search_bar = search_bar  # 검색창 저장
        tab.current_path = path
        tab.item_count_label = item_count_label
        tab.selected_label = selected_label
        tab.refresh_btn = refresh_btn  # 새로고침 버튼 저장
        tab.back_btn = back_btn        # 뒤로 버튼 저장 (탭별 활성화 제어용)
        tab.forward_btn = forward_btn  # 앞으로 버튼 저장 (탭별 활성화 제어용)
        tab.up_btn = up_btn            # 상위폴더 버튼 저장
        # 탭별 독립 히스토리 초기화
        tab.nav_history = []
        tab.nav_history_index = -1
        tab._size_request_id = 0
        tab._size_target_path = ""
        
        # 이벤트 연결
        tree.doubleClicked.connect(lambda idx: self.on_tree_double_clicked(tab, idx, side))
        tree.selectionModel().selectionChanged.connect(lambda *_: self.schedule_update_status(tab))
        tree.expanded.connect(lambda idx: self.on_tree_expanded(tab, idx))  # 폴더 확장 시 새로고침
        
        # 초기 상태 업데이트
        self.update_status(tab)
        
        # 탭 툴팁: 전체 경로를 호버 시 표시
        tab_idx = tab_widget.indexOf(tab)
        if tab_idx >= 0:
            tab_widget.setTabToolTip(tab_idx, self.normalize_path(path))
        
        # 초기 경로를 탭별 히스토리에 추가 (뒤로/앞으로 버튼이 작동하도록)
        if os.path.exists(path) and os.path.isdir(path):
            tab.nav_history.append(path)
            tab.nav_history_index = 0
        
        # 초기 버튼 상태 업데이트
        self._update_nav_buttons(tab)
        
        return tab

    # -------------------------------------------------------------------------
    # 탭 이름 표시용 헬퍼: 최대 16자, 초과 시 앞 7자 + '...' + 뒤 6자
    # -------------------------------------------------------------------------
    TAB_NAME_MAX = 16

    def _tab_display_name(self, folder_name: str) -> str:
        """폴더 이름이 TAB_NAME_MAX 자를 초과하면 '앞...뒤' 형식으로 줄인다."""
        if len(folder_name) <= self.TAB_NAME_MAX:
            return folder_name
        return folder_name[:7] + "…" + folder_name[-6:]

    # -------------------------------------------------------------------------
    # 탭 내 검색창 콜백
    # -------------------------------------------------------------------------
    def _on_search_changed(self, tab, text):
        """
        검색창 텍스트 변경 시 호출
        - 현재 tab.current_path 를 루트로 삼아 proxy model 에 필터 적용
        - 검색어가 있으면 매칭 항목이 보이도록 트리 전체 펼침
        - 검색어가 없으면 모두 접기로 초기 상태 복원
        [수정] collapseAll() 호출 후 invalidateFilter()로 인해 rootIndex가
               초기화되는 문제를 방지하기 위해 루트 인덱스를 명시적으로 재설정
        """
        if not hasattr(tab, 'model') or not hasattr(tab, 'current_path'):
            return
        root = tab.current_path
        tab.model.set_filter_text(text, root_path=root)
        if text.strip():
            tab.tree.expandAll()
        else:
            tab.tree.collapseAll()
            # collapseAll() + invalidateFilter() 조합으로 rootIndex가
            # 프록시 모델 최상위(드라이브 목록)로 초기화되는 현상 방지
            source_index = tab.source_model.index(root)
            proxy_index = tab.model.mapFromSource(source_index)
            tab.tree.setRootIndex(proxy_index)

    def navigate_to(self, tab, path, side):
        """경로로 이동 (탭별 히스토리 저장)"""
        if os.path.exists(path) and os.path.isdir(path):
            # 탭별 히스토리가 없으면 초기화 (구버전 탭 호환)
            if not hasattr(tab, 'nav_history'):
                tab.nav_history = []
                tab.nav_history_index = -1
            
            # 현재 경로를 히스토리에 추가
            if tab.current_path != path:
                # 현재 인덱스 이후의 히스토리 삭제 (앞으로 가기 초기화)
                tab.nav_history = tab.nav_history[:tab.nav_history_index + 1]
                tab.nav_history.append(path)
                tab.nav_history_index = len(tab.nav_history) - 1
                
            tab.current_path = path
            # 경로 이동 시 검색창 초기화 (검색 필터 해제)
            if hasattr(tab, 'search_bar') and tab.search_bar.text():
                tab.search_bar.blockSignals(True)
                tab.search_bar.clear()
                tab.search_bar.blockSignals(False)
                tab.model.set_filter_text("", root_path=path)
            # 해당 디렉토리 강제 새로고침 (새 파일 감지)
            tab.source_model.refresh_directory(path)
            # 프록시 모델을 통해 인덱스 설정
            source_index = tab.source_model.index(path)
            proxy_index = tab.model.mapFromSource(source_index)
            tab.tree.setRootIndex(proxy_index)
            tab.address_bar.setText(self.normalize_path(path))
            self.update_status(tab)
            
            # 탭 이름 업데이트 (현재 디렉토리의 마지막 폴더 이름)
            tab_widget = self.left_tabs if side == 'left' else self.right_tabs
            current_index = tab_widget.indexOf(tab)
            if current_index >= 0:
                folder_name = os.path.basename(path) or path
                tab_widget.setTabText(current_index, self._tab_display_name(folder_name))
                tab_widget.setTabToolTip(current_index, self.normalize_path(path))
            
            # 뒤로/앞으로/상위폴더 버튼 상태 업데이트
            self._update_nav_buttons(tab)
    
    def navigate_back(self, tab, side):
        """뒤로 가기 (탭별 히스토리)"""
        if not hasattr(tab, 'nav_history'):
            return
        if tab.nav_history_index > 0:
            tab.nav_history_index -= 1
            path = tab.nav_history[tab.nav_history_index]
            tab.current_path = path
            # 검색창 초기화
            if hasattr(tab, 'search_bar') and tab.search_bar.text():
                tab.search_bar.blockSignals(True)
                tab.search_bar.clear()
                tab.search_bar.blockSignals(False)
                tab.model.set_filter_text("", root_path=path)
            # 프록시 모델을 통해 인덱스 설정
            source_index = tab.source_model.index(path)
            proxy_index = tab.model.mapFromSource(source_index)
            tab.tree.setRootIndex(proxy_index)
            tab.address_bar.setText(self.normalize_path(path))
            self.update_status(tab)
            # 탭 이름 업데이트
            tab_widget = self.left_tabs if side == 'left' else self.right_tabs
            current_index = tab_widget.indexOf(tab)
            if current_index >= 0:
                folder_name = os.path.basename(path) or path
                tab_widget.setTabText(current_index, self._tab_display_name(folder_name))
                tab_widget.setTabToolTip(current_index, self.normalize_path(path))
            # 버튼 상태 업데이트
            self._update_nav_buttons(tab)
    
    def navigate_forward(self, tab, side):
        """앞으로 가기 (탭별 히스토리)"""
        if not hasattr(tab, 'nav_history'):
            return
        if tab.nav_history_index < len(tab.nav_history) - 1:
            tab.nav_history_index += 1
            path = tab.nav_history[tab.nav_history_index]
            tab.current_path = path
            # 검색창 초기화
            if hasattr(tab, 'search_bar') and tab.search_bar.text():
                tab.search_bar.blockSignals(True)
                tab.search_bar.clear()
                tab.search_bar.blockSignals(False)
                tab.model.set_filter_text("", root_path=path)
            # 프록시 모델을 통해 인덱스 설정
            source_index = tab.source_model.index(path)
            proxy_index = tab.model.mapFromSource(source_index)
            tab.tree.setRootIndex(proxy_index)
            tab.address_bar.setText(self.normalize_path(path))
            self.update_status(tab)
            # 탭 이름 업데이트
            tab_widget = self.left_tabs if side == 'left' else self.right_tabs
            current_index = tab_widget.indexOf(tab)
            if current_index >= 0:
                folder_name = os.path.basename(path) or path
                tab_widget.setTabText(current_index, self._tab_display_name(folder_name))
                tab_widget.setTabToolTip(current_index, self.normalize_path(path))
            # 버튼 상태 업데이트
            self._update_nav_buttons(tab)
    
    def on_tree_item_activated(self, tab, index, side):
        """트리뷰 아이템 활성화 (더블클릭 또는 엔터 키)"""
        # 프록시 인덱스를 소스 인덱스로 변환
        source_index = tab.model.mapToSource(index)
        path = tab.source_model.filePath(source_index)
        if os.path.isdir(path):
            self.navigate_to(tab, path, side)
        else:
            # 파일 실행
            self.open_file(path)
    
    def on_tree_double_clicked(self, tab, index, side):
        """트리뷰 더블클릭 - 메모 컬럼(4번)은 편집만, 다른 컬럼은 파일/폴더 열기"""
        # 메모 컬럼(4번)인 경우 편집만 진행하고 폴더/파일 열기는 하지 않음
        if index.column() == 4:
            # 메모 컬럼은 Qt의 기본 편집 동작만 수행
            return
        
        # 다른 컬럼은 기존처럼 폴더 진입 또는 파일 열기
        self.on_tree_item_activated(tab, index, side)
    
    def on_tree_expanded(self, tab, index):
        """트리뷰 폴더 확장 시 - 해당 폴더의 내용 강제 새로고침"""
        # 프록시 인덱스를 소스 인덱스로 변환
        source_index = tab.model.mapToSource(index)
        folder_path = tab.source_model.filePath(source_index)
        
        # 폴더인 경우에만 새로고침
        if os.path.isdir(folder_path):
            now = time.monotonic()
            last = self._last_expand_refresh.get(folder_path, 0.0)
            # 짧은 시간 내 반복 확장 시 중복 새로고침 방지
            if now - last >= 1.2:
                self._last_expand_refresh[folder_path] = now
                tab.source_model.refresh_directory(folder_path)
    
    def open_file(self, path):
        """파일 열기"""
        try:
            if platform.system() == 'Windows':
                os.startfile(path)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', path])
            else:  # Linux
                subprocess.run(['xdg-open', path])
        except Exception as e:
            QMessageBox.warning(self, "오류", f"파일을 열 수 없습니다\n{e}")
    
    def handle_drop(self, tab, mime_data, drop_index, is_copy):
        """드래그 앤 드롭 처리 (파일/폴더 이동 또는 복사)"""
        if not mime_data.hasUrls():
            return
        
        # 드롭 대상 경로 결정
        if drop_index.isValid():
            # 프록시 인덱스를 소스 인덱스로 변환
            source_index = tab.model.mapToSource(drop_index)
            drop_path = tab.source_model.filePath(source_index)
            # 파일에 드롭한 경우 해당 파일의 부모 디렉토리 사용
            if os.path.isfile(drop_path):
                drop_path = os.path.dirname(drop_path)
        else:
            # 빈 공간에 드롭한 경우 현재 디렉토리 사용
            drop_path = tab.current_path
        
        if not os.path.isdir(drop_path):
            return
        
        # 드래그한 파일/폴더 목록
        source_paths = [url.toLocalFile() for url in mime_data.urls()]
        
        if not source_paths:
            return
        
        # 작업 타입 결정
        operation = "복사" if is_copy else "이동"
        
        # 확인 대화상자
        reply = QMessageBox.question(
            self, "확인",
            f"{len(source_paths)}개 항목을 다음 위치로 {operation}하시겠습니까?\n\n{drop_path}",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 진행률 표시
        progress = QProgressDialog(f"{operation} 중...", "취소", 0, len(source_paths), self)
        progress.setWindowModality(Qt.WindowModal)
        
        success_count = 0
        failed_items = []
        
        for i, src_path in enumerate(source_paths):
            if progress.wasCanceled():
                break
            
            progress.setValue(i)
            progress.setLabelText(f"{operation} 중: {os.path.basename(src_path)}")
            
            try:
                # 대상 경로 생성
                dest_path = os.path.join(drop_path, os.path.basename(src_path))
                
                # 동일 경로인 경우 건너뛰기
                if os.path.normpath(src_path) == os.path.normpath(dest_path):
                    continue
                
                # 동일 이름 파일이 존재하는 경우 처리
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(dest_path)
                    counter = 1
                    while os.path.exists(f"{base} ({counter}){ext}"):
                        counter += 1
                    dest_path = f"{base} ({counter}){ext}"
                
                # 복사 또는 이동
                if is_copy:
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                    
                    # 메모도 복사
                    tab.source_model.copy_memo(src_path, dest_path)
                    
                    # 복사 작업은 실행 취소 스택에 추가 (삭제로 취소 가능)
                    self.undo_stack.append({
                        'type': 'copy',
                        'created_path': dest_path
                    })
                else:
                    shutil.move(src_path, dest_path)
                    
                    # 메모도 이동
                    tab.source_model.move_memo(src_path, dest_path)
                    
                    # 이동 작업은 실행 취소 스택에 추가
                    self.undo_stack.append({
                        'type': 'move',
                        'old_path': src_path,
                        'new_path': dest_path
                    })
                
                success_count += 1
                
            except Exception as e:
                failed_items.append(f"{os.path.basename(src_path)}: {str(e)}")
        
        progress.setValue(len(source_paths))
        
        # 뷰 새로고침
        self.refresh_view(tab)
        
        # 결과 메시지
        if success_count > 0:
            self.status_label.setText(f"{success_count}개 항목 {operation} 완료")
        
        if failed_items:
            error_msg = f"{operation} 실패한 항목:\n\n" + "\n".join(failed_items[:5])
            if len(failed_items) > 5:
                error_msg += f"\n... 외 {len(failed_items) - 5}개"
            QMessageBox.warning(self, f"{operation} 오류", error_msg)
    
    def on_address_changed(self, tab, side):
        """주소창 변경"""
        path = tab.address_bar.text()
        if os.path.exists(path) and os.path.isdir(path):
            self.navigate_to(tab, path, side)
        else:
            QMessageBox.warning(self, "경고", "존재하지 않는 경로입니다.")
            tab.address_bar.setText(self.normalize_path(tab.current_path))
    
    def go_up(self, tab, side):
        """상위 폴더로 이동"""
        parent = os.path.dirname(tab.current_path)
        if parent and parent != tab.current_path:
            self.navigate_to(tab, parent, side)
    
    def _update_nav_buttons(self, tab):
        """
        뒤로/앞으로/상위폴더 버튼의 활성화 상태를 현재 탭 히스토리에 맞게 갱신
        - 뒤로(◀): 히스토리 인덱스 > 0 일 때 활성
        - 앞으로(▶): 히스토리 인덱스 < 마지막 인덱스 일 때 활성
        - 상위폴더(⬆): 부모 경로가 현재 경로와 다를 때 활성
        """
        if not hasattr(tab, 'nav_history'):
            return
        try:
            # 뒤로 버튼
            if hasattr(tab, 'back_btn'):
                can_back = tab.nav_history_index > 0
                tab.back_btn.setEnabled(can_back)
                if can_back:
                    prev = tab.nav_history[tab.nav_history_index - 1]
                    tab.back_btn.setToolTip(f"뒤로: {self.normalize_path(prev)}")
                else:
                    tab.back_btn.setToolTip("뒤로 (이전 방문 폴더)")
            # 앞으로 버튼
            if hasattr(tab, 'forward_btn'):
                can_forward = tab.nav_history_index < len(tab.nav_history) - 1
                tab.forward_btn.setEnabled(can_forward)
                if can_forward:
                    nxt = tab.nav_history[tab.nav_history_index + 1]
                    tab.forward_btn.setToolTip(f"앞으로: {self.normalize_path(nxt)}")
                else:
                    tab.forward_btn.setToolTip("앞으로 (다음 방문 폴더)")
            # 상위폴더 버튼
            if hasattr(tab, 'up_btn'):
                cur = getattr(tab, 'current_path', '')
                parent = os.path.dirname(cur) if cur else ''
                can_up = bool(parent) and parent != cur
                tab.up_btn.setEnabled(can_up)
                if can_up:
                    tab.up_btn.setToolTip(f"상위 폴더: {self.normalize_path(parent)}")
                else:
                    tab.up_btn.setToolTip("상위 폴더 (부모 폴더로 이동)")
        except Exception:
            pass

    def collapse_all(self, tab=None):
        """현재 활성 탭의 트리뷰에서 모든 폴더를 일괄 접기"""
        if tab is None:
            tab = self.active_tab_widget.currentWidget()
        if tab and hasattr(tab, 'tree'):
            tab.tree.collapseAll()
            self.status_label.setText("모든 폴더 접힘")

    def expand_all(self, tab=None):
        """현재 활성 탭의 트리뷰에서 모든 폴더를 일괄 펼치기"""
        if tab is None:
            tab = self.active_tab_widget.currentWidget()
        if tab and hasattr(tab, 'tree'):
            tab.tree.expandAll()
            self.status_label.setText("모든 폴더 펼침")

    def refresh_view_with_animation(self, tab, refresh_btn):
        """애니메이션과 함께 새로고침"""
        # 회전 애니메이션 시작
        if refresh_btn and hasattr(refresh_btn, 'start_rotation_animation'):
            refresh_btn.start_rotation_animation()
        
        # 상태바 메시지 표시
        self.status_label.setText("🔄 새로고침 중...")
        
        # 실제 새로고침 실행
        self.refresh_view(tab)
        
        # 완료 메시지 (짧은 지연 후)
        QTimer.singleShot(650, lambda: self.status_label.setText("✅ 새로고침 완료!"))
        QTimer.singleShot(2650, lambda: self.status_label.setText("준비"))
    
    def refresh_view(self, tab):
        """새로고침 - 파일 시스템 모델 캐시를 완전히 갱신"""
        if not tab or not hasattr(tab, 'current_path'):
            return
        
        current_path = tab.current_path
        
        # 현재 선택된 항목 저장 (새로고침 후 복원)
        selected_paths = []
        selected_indexes = tab.tree.selectionModel().selectedIndexes()
        if selected_indexes:
            for idx in selected_indexes:
                if idx.column() == 0:  # 첫 번째 컬럼만
                    source_idx = tab.model.mapToSource(idx)
                    path = tab.source_model.filePath(source_idx)
                    selected_paths.append(path)
        
        # 현재 스크롤 위치 저장
        scrollbar = tab.tree.verticalScrollBar()
        scroll_position = scrollbar.value()
        
        # 확장된 항목 저장
        expanded_paths = []
        def save_expanded(parent_idx, parent_path):
            row_count = tab.model.rowCount(parent_idx)
            for row in range(row_count):
                child_idx = tab.model.index(row, 0, parent_idx)
                if tab.tree.isExpanded(child_idx):
                    source_idx = tab.model.mapToSource(child_idx)
                    child_path = tab.source_model.filePath(source_idx)
                    expanded_paths.append(child_path)
                    save_expanded(child_idx, child_path)
        
        root_idx = tab.tree.rootIndex()
        save_expanded(root_idx, current_path)
        
        # 모델 강제 갱신 - 캐시를 완전히 비우고 다시 읽기
        tab.source_model.setRootPath("")  # 일시적으로 초기화
        tab.source_model.setRootPath(current_path)  # 다시 설정하여 강제 갱신
        
        # 프록시 모델을 통해 인덱스 재설정
        source_index = tab.source_model.index(current_path)
        proxy_index = tab.model.mapFromSource(source_index)
        tab.tree.setRootIndex(proxy_index)
        
        # 정렬 상태 유지
        header = tab.tree.header()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        tab.tree.sortByColumn(sort_column, sort_order)
        
        # 확장된 항목 복원 (약간의 지연 후)
        from PySide6.QtCore import QTimer
        def restore_expanded():
            for expanded_path in expanded_paths:
                source_idx = tab.source_model.index(expanded_path)
                if source_idx.isValid():
                    proxy_idx = tab.model.mapFromSource(source_idx)
                    if proxy_idx.isValid():
                        tab.tree.setExpanded(proxy_idx, True)
        
        # 선택 항목 복원
        def restore_selection():
            tab.tree.clearSelection()
            selection_model = tab.tree.selectionModel()
            for selected_path in selected_paths:
                source_idx = tab.source_model.index(selected_path)
                if source_idx.isValid():
                    proxy_idx = tab.model.mapFromSource(source_idx)
                    if proxy_idx.isValid():
                        selection_model.select(proxy_idx, selection_model.Select | selection_model.Rows)
        
        # 스크롤 위치 복원
        def restore_scroll():
            scrollbar.setValue(scroll_position)
        
        # 복원 작업을 순차적으로 실행
        QTimer.singleShot(50, restore_expanded)
        QTimer.singleShot(100, restore_selection)
        QTimer.singleShot(150, restore_scroll)
        
        # 상태 업데이트
        self.update_status(tab)
    
    def refresh_current_directories(self):
        """현재 표시 중인 모든 탭의 디렉토리 정보를 갱신 (타이머에서 주기적으로 호출)"""
        # 백그라운드/최소화 상태에서는 주기 갱신을 건너뛰어 UI 부하를 줄임
        if self.isMinimized() or not self.isVisible():
            return

        # 왼쪽 탭들 갱신
        for i in range(self.left_tabs.count()):
            tab = self.left_tabs.widget(i)
            if tab and hasattr(tab, 'source_model') and hasattr(tab, 'current_path') and hasattr(tab, 'tree'):
                # 편집 중인지 확인 - 편집 중이면 새로고침 건너뛰기
                if tab.tree.state() == QAbstractItemView.EditingState:
                    continue
                # 현재 디렉토리의 파일 정보를 갱신 (조용히, UI 방해 없이)
                if os.path.exists(tab.current_path):
                    tab.source_model.refresh_directory(tab.current_path)

        # 오른쪽 탭들 갱신
        for i in range(self.right_tabs.count()):
            tab = self.right_tabs.widget(i)
            if tab and hasattr(tab, 'source_model') and hasattr(tab, 'current_path') and hasattr(tab, 'tree'):
                # 편집 중인지 확인 - 편집 중이면 새로고침 건너뛰기
                if tab.tree.state() == QAbstractItemView.EditingState:
                    continue
                # 현재 디렉토리의 파일 정보를 갱신 (조용히, UI 방해 없이)
                if os.path.exists(tab.current_path):
                    tab.source_model.refresh_directory(tab.current_path)

    def schedule_update_status(self, tab, delay_ms=80):
        """선택 변경 시 상태바 갱신을 디바운스해 연속 이벤트 부하를 줄인다."""
        if not tab:
            return
        if not hasattr(tab, '_status_update_timer'):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda t=tab: self.update_status(t))
            tab._status_update_timer = timer
        tab._status_update_timer.start(max(0, int(delay_ms)))

    def _invalidate_size_request(self, tab):
        """기존 파일 크기 요청을 무효화한다 (스레드 중단 대신 결과 무시)."""
        if not tab:
            return
        self._size_request_seq += 1
        tab._size_request_id = self._size_request_seq
        tab._size_target_path = ""

    def _request_file_size_async(self, tab, file_path):
        """파일 크기 계산을 워커 스레드로 위임한다."""
        if not tab or not file_path:
            return

        self._size_request_seq += 1
        current_id = self._size_request_seq
        tab._size_request_id = current_id
        tab._size_target_path = file_path

        worker = FileSizeWorker(current_id, file_path, self)
        worker.size_ready.connect(lambda rid, path, size, t=tab: self._on_file_size_ready(t, rid, path, size))
        worker.finished.connect(lambda rid=current_id: self._size_workers.pop(rid, None))
        self._size_workers[current_id] = worker
        worker.start()

    def _on_file_size_ready(self, tab, request_id, file_path, size):
        """비동기 파일 크기 계산 결과를 현재 선택 상태와 매칭해 반영한다."""
        if not tab:
            return
        if request_id != getattr(tab, '_size_request_id', -1):
            return
        if file_path != getattr(tab, '_size_target_path', None):
            return

        selected = tab.tree.selectionModel().selectedIndexes() if hasattr(tab, 'tree') else []
        row_count = len(set(idx.row() for idx in selected)) if selected else 0
        if row_count != 1:
            return

        if size >= 0:
            tab.selected_label.setText(f"1 개 선택 ({self.format_size(size)})")
        else:
            tab.selected_label.setText("1 개 선택")
    
    def update_status(self, tab):
        """상태 정보 업데이트"""
        try:
            # 항목 개수: os.listdir 대신 모델 rowCount를 사용해 반복 I/O를 줄임
            root_idx = tab.tree.rootIndex()
            item_count = tab.model.rowCount(root_idx) if root_idx.isValid() else 0
            tab.item_count_label.setText(f"{item_count} 개 항목")
            
            # 선택된 항목
            selected = tab.tree.selectionModel().selectedIndexes()
            if selected:
                unique_rows = set(idx.row() for idx in selected)
                count = len(unique_rows)
                if count == 1:
                    col0_index = next((idx for idx in selected if idx.column() == 0), selected[0])
                    source_index = tab.model.mapToSource(col0_index)
                    path = tab.source_model.filePath(source_index)
                    if os.path.isfile(path):
                        # 파일 크기 계산은 비동기로 처리하여 클릭 시 UI 블로킹 방지
                        tab.selected_label.setText("1 개 선택 (크기 계산 중...)")
                        if path != getattr(tab, '_size_target_path', ""):
                            self._request_file_size_async(tab, path)
                    else:
                        self._invalidate_size_request(tab)
                        tab.selected_label.setText(f"1 개 선택")
                else:
                    self._invalidate_size_request(tab)
                    tab.selected_label.setText(f"{count} 개 선택")
            else:
                self._invalidate_size_request(tab)
                tab.selected_label.setText("")
        except:
            pass
    
    def format_size(self, size):
        """파일 크기 포맷"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
    
    def show_context_menu(self, tree, pos, tab):
        """컨텍스트 메뉴 표시"""
        
        # 1. Shift 키가 눌렸는지 감지
        modifiers = QApplication.keyboardModifiers()
        is_shift_pressed = bool(modifiers & Qt.ShiftModifier)

        index = tree.indexAt(pos)
        selected_indexes = tree.selectionModel().selectedIndexes()
        
        # 2. Shift + 우클릭인 경우의 동작 (윈도우 탐색기 호출)
        if is_shift_pressed and platform.system() == 'Windows':
            if index.isValid():
                # 파일이나 폴더를 클릭했을 때 -> 해당 항목이 선택된 상태로 탐색기 열기
                source_index = tab.model.mapToSource(index)
                target_path = tab.source_model.filePath(source_index)
                subprocess.run(['explorer', '/select,', os.path.normpath(target_path)])
            else:
                # 빈 공간을 클릭했을 때 -> 현재 열려있는 폴더를 탐색기에서 열기
                subprocess.run(['explorer', os.path.normpath(tab.current_path)])
            return  # 윈도우 탐색기를 띄웠으므로 커스텀 메뉴 코드는 무시하고 종료

        # ==========================================================
        # 3. Shift를 누르지 않았을 때: 기존 커스텀 메뉴 코드 실행
        # ==========================================================
        menu = QMenu(self)
        
        if index.isValid():
            # 파일/폴더 선택된 경우
            open_action = QAction("열기", self)
            open_action.triggered.connect(lambda: self.open_selected(tab))
            menu.addAction(open_action)
            
            menu.addSeparator()
            
            copy_action = QAction("복사", self)
            copy_action.triggered.connect(self.copy_files)
            menu.addAction(copy_action)
            
            cut_action = QAction("잘라내기", self)
            cut_action.triggered.connect(self.cut_files)
            menu.addAction(cut_action)
            
            paste_action = QAction("붙여넣기", self)
            paste_action.triggered.connect(self.paste_files)
            paste_action.setEnabled(len(self.clipboard_items) > 0)
            menu.addAction(paste_action)
            
            menu.addSeparator()
            
            delete_action = QAction("삭제", self)
            delete_action.triggered.connect(self.delete_files)
            menu.addAction(delete_action)
            
            rename_action = QAction("이름 바꾸기", self)
            rename_action.triggered.connect(self.rename_file)
            menu.addAction(rename_action)
            
            # 메모 편집
            memo_action = QAction("📝 메모 편집 (F3)", self)
            memo_action.triggered.connect(self.edit_memo)
            menu.addAction(memo_action)

            # 경로 복사
            copy_path_action = QAction("경로 복사", self)
            copy_path_action.triggered.connect(self.copy_full_path)
            menu.addAction(copy_path_action)
            
            # 프록시 인덱스를 소스 인덱스로 변환
            source_index = tab.model.mapToSource(index)
            selected_path = tab.source_model.filePath(source_index)
            
            # 선택된 파일/폴더 경로 수집
            selected_paths = []
            for idx in selected_indexes:
                if idx.column() == 0:
                    src_idx = tab.model.mapToSource(idx)
                    selected_paths.append(tab.source_model.filePath(src_idx))
            
            # 파일/폴더 선택 시 zip 압축 메뉴 추가 (1개 이상)
            if len(selected_paths) >= 1:
                menu.addSeparator()
                compress_action = QAction("🗜️ ZIP으로 압축", self)
                compress_action.triggered.connect(lambda: self.compress_to_zip(selected_paths, tab))
                menu.addAction(compress_action)
            
            # 폴더인 경우만 추가 메뉴
            if os.path.isdir(selected_path):
                menu.addSeparator()
                
                # 하위 새폴더 생성
                create_subfolder_action = QAction("📁 하위 새폴더 생성", self)
                create_subfolder_action.triggered.connect(lambda checked=False, p=selected_path: self.create_subfolder_in_path(p, tab))
                menu.addAction(create_subfolder_action)

                # 동일 폴더 생성
                same_folder_action = QAction("📁 동일 폴더 생성", self)
                same_folder_action.triggered.connect(lambda checked=False, p=selected_path, t=tab: self.create_same_folder(p, t))
                menu.addAction(same_folder_action)
                
                # 폴더 비우기
                empty_folder_action = QAction("🗑️ 폴더 비우기", self)
                empty_folder_action.triggered.connect(lambda checked=False, p=selected_path: self.empty_folder(p, tab))
                menu.addAction(empty_folder_action)
                
                # 즐겨찾기에 추가
                add_fav_action = QAction("⭐ 즐겨찾기에 추가", self)
                add_fav_action.triggered.connect(lambda checked=False, p=selected_path: self.add_favorite_path(p))
                menu.addAction(add_fav_action)
            
            # zip 파일인 경우 압축 해제 메뉴 추가
            if selected_path.lower().endswith('.zip'):
                menu.addSeparator()
                
                extract_here_action = QAction("📂 현재 폴더에 압축 해제", self)
                extract_here_action.triggered.connect(lambda: self.extract_zip_here(selected_path, tab))
                menu.addAction(extract_here_action)
                
                extract_folder_action = QAction("📁 폴더 생성 후 압축 해제", self)
                extract_folder_action.triggered.connect(lambda: self.extract_zip_to_folder(selected_path, tab))
                menu.addAction(extract_folder_action)

            menu.addSeparator()
            
            properties_action = QAction("속성", self)
            properties_action.triggered.connect(lambda: self.show_properties(tab))
            menu.addAction(properties_action)
        else:
            # 빈 공간 클릭
            paste_action = QAction("붙여넣기", self)
            paste_action.triggered.connect(self.paste_files)
            paste_action.setEnabled(len(self.clipboard_items) > 0)
            menu.addAction(paste_action)
            
            menu.addSeparator()
            
            new_folder_action = QAction("새 폴더", self)
            new_folder_action.triggered.connect(self.create_new_folder)
            menu.addAction(new_folder_action)
            
            menu.addSeparator()
            
            # 현재 폴더 경로 복사
            copy_current_path_action = QAction("경로 복사", self)
            copy_current_path_action.triggered.connect(lambda: self.copy_current_folder_path(tab))
            menu.addAction(copy_current_path_action)
            
            # 현재 폴더를 즐겨찾기에 추가
            add_fav_action = QAction("⭐ 이 폴더를 즐겨찾기에 추가", self)
            add_fav_action.triggered.connect(self.add_favorite)
            menu.addAction(add_fav_action)
        
        menu.exec_(tree.viewport().mapToGlobal(pos))
    
    def open_selected(self, tab):
        """선택된 항목 열기"""
        selected = tab.tree.selectionModel().selectedIndexes()
        if selected:
            # 프록시 인덱스를 소스 인덱스로 변환
            source_index = tab.model.mapToSource(selected[0])
            path = tab.source_model.filePath(source_index)
            if os.path.isdir(path):
                self.navigate_to(tab, path, self.active_side)
            else:
                self.open_file(path)
    
    def copy_files(self):
        """파일 복사"""
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        selected = current_tab.tree.selectionModel().selectedIndexes()
        if selected:
            self.clipboard_items = []
            for idx in selected:
                if idx.column() == 0:
                    # 프록시 인덱스를 소스 인덱스로 변환
                    source_index = current_tab.model.mapToSource(idx)
                    path = current_tab.source_model.filePath(source_index)
                    self.clipboard_items.append(path)
            
            self.clipboard_operation = 'copy'
            self.status_label.setText(f"{len(self.clipboard_items)} 개 항목 복사됨")
    
    def cut_files(self):
        """파일 잘라내기"""
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        selected = current_tab.tree.selectionModel().selectedIndexes()
        if selected:
            self.clipboard_items = []
            for idx in selected:
                if idx.column() == 0:
                    # 프록시 인덱스를 소스 인덱스로 변환
                    source_index = current_tab.model.mapToSource(idx)
                    path = current_tab.source_model.filePath(source_index)
                    self.clipboard_items.append(path)
            
            self.clipboard_operation = 'cut'
            self.status_label.setText(f"{len(self.clipboard_items)} 개 항목 잘라내기")
    
    def paste_files(self):
        """파일 붙여넣기"""
        if not self.clipboard_items:
            return
        
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        # 붙여넣기 대상 경로 결정
        dest_dir = None
        selected = current_tab.tree.selectionModel().selectedIndexes()
        
        if selected:
            # 선택된 항목이 있는 경우
            # 첫 번째 선택 항목 확인 (column 0만)
            for idx in selected:
                if idx.column() == 0:
                    # 프록시 인덱스를 소스 인덱스로 변환
                    source_index = current_tab.model.mapToSource(idx)
                    selected_path = current_tab.source_model.filePath(source_index)
                    
                    if os.path.isdir(selected_path):
                        # 폴더가 선택된 경우: 해당 폴더를 대상으로
                        dest_dir = selected_path
                    else:
                        # 파일이 선택된 경우: 파일의 부모 폴더를 대상으로
                        dest_dir = os.path.dirname(selected_path)
                    break
        
        if dest_dir is None:
            # 아무것도 선택되지 않은 경우 (빈 공간 선택)
            # 트리뷰의 현재 루트 경로를 대상으로
            dest_dir = current_tab.current_path
        
        progress = QProgressDialog("파일 처리 중...", "취소", 0, len(self.clipboard_items), self)
        progress.setWindowModality(Qt.WindowModal)
        
        # 실행 취소를 위해 생성된 파일 경로 목록 저장
        created_paths = []
        
        for i, src_path in enumerate(self.clipboard_items):
            if progress.wasCanceled():
                break
            
            progress.setValue(i)
            progress.setLabelText(f"처리 중: {os.path.basename(src_path)}")
            
            try:
                dest_path = os.path.join(dest_dir, os.path.basename(src_path))
                
                # 동일 이름 처리
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(dest_path)
                    counter = 1
                    while os.path.exists(f"{base} ({counter}){ext}"):
                        counter += 1
                    dest_path = f"{base} ({counter}){ext}"
                
                if self.clipboard_operation == 'copy':
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                    # 복사된 파일/폴더 경로를 실행 취소 스택에 추가
                    created_paths.append(dest_path)
                    # 메모도 복사
                    current_tab.source_model.copy_memo(src_path, dest_path)
                elif self.clipboard_operation == 'cut':
                    shutil.move(src_path, dest_path)
                    # 이동 작업은 개별적으로 실행 취소 스택에 추가
                    self.undo_stack.append({
                        'type': 'move',
                        'old_path': src_path,
                        'new_path': dest_path
                    })
                    # 메모도 이동
                    current_tab.source_model.move_memo(src_path, dest_path)
            except Exception as e:
                QMessageBox.warning(self, "오류", f"파일 처리 실패\n{e}")
        
        progress.setValue(len(self.clipboard_items))
        
        # 복사 작업인 경우, 모든 생성된 파일을 하나의 실행 취소 항목으로 추가
        if self.clipboard_operation == 'copy' and created_paths:
            for created_path in created_paths:
                self.undo_stack.append({
                    'type': 'copy',
                    'created_path': created_path
                })
        
        if self.clipboard_operation == 'cut':
            self.clipboard_items = []
        
        self.refresh_view(current_tab)
        self.status_label.setText("완료")
    
    def delete_files(self):
        """
        파일 삭제 (트리뷰에 포커스가 있을 때만 실행)
        [프로세스]
        1. 현재 포커스된 위젯이 트리뷰인지 확인
        2. 트리뷰가 아니면 실행하지 않음 (의도치 않은 삭제 방지)
        3. 선택된 파일/폴더를 휴지통으로 이동
        """
        # 현재 포커스된 위젯 확인
        focused_widget = QApplication.focusWidget()
        
        # 트리뷰에 포커스가 없으면 삭제 작업 수행하지 않음
        if not isinstance(focused_widget, CustomTreeView):
            return
        
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        selected = current_tab.tree.selectionModel().selectedIndexes()
        if not selected:
            return
        
        paths = []
        for idx in selected:
            if idx.column() == 0:
                # 프록시 인덱스를 소스 인덱스로 변환
                source_index = current_tab.model.mapToSource(idx)
                path = current_tab.source_model.filePath(source_index)
                paths.append(path)
        
        if not paths:
            return
        
        reply = QMessageBox.question(
            self, "확인", 
            f"{len(paths)} 개 항목을 휴지통으로 이동하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            failed_items = []
            success_count = 0
            
            for path in paths:
                try:
                    # 경로 정규화 (백슬래시 형식으로 변환)
                    normalized_path = os.path.normpath(path)
                    
                    # 파일/폴더 존재 확인
                    if not os.path.exists(normalized_path):
                        failed_items.append(f"{path}\n(존재하지 않음)")
                        continue
                    
                    # 휴지통으로 이동
                    send2trash(normalized_path)
                    success_count += 1
                    
                except Exception as e:
                    failed_items.append(f"{path}\n({str(e)})")
            
            # 결과 메시지
            if success_count > 0:
                self.refresh_view(current_tab)
                self.status_label.setText(f"{success_count} 개 항목 삭제됨")
            
            if failed_items:
                error_msg = f"삭제 실패한 항목 ({len(failed_items)}개):\n\n"
                error_msg += "\n\n".join(failed_items[:5])  # 최대 5개만 표시
                if len(failed_items) > 5:
                    error_msg += f"\n\n... 외 {len(failed_items) - 5}개"
                QMessageBox.warning(self, "삭제 오류", error_msg)
    
    def rename_file(self):
        """파일 이름 바꾸기 — F3(메모 편집)과 동일하게 트리뷰 인라인 편집"""
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return

        selected = current_tab.tree.selectionModel().selectedIndexes()
        col0_indexes = [idx for idx in selected if idx.column() == 0]
        if not col0_indexes or len(col0_indexes) != 1:
            QMessageBox.warning(self, "경고", "항목을 하나만 선택하세요.")
            return

        proxy_index = col0_indexes[0]
        # Name 컬럼(0) 인덱스로 인라인 편집 시작
        current_tab.tree.setCurrentIndex(proxy_index)
        current_tab.tree.edit(proxy_index)

        # 편집기가 열린 직후 확장자 이전 부분만 선택
        def select_without_extension():
            editor = current_tab.tree.focusWidget()
            if isinstance(editor, QLineEdit):
                source_index = current_tab.model.mapToSource(proxy_index)
                file_path = current_tab.source_model.filePath(source_index)
                name = os.path.basename(file_path)
                if os.path.isfile(file_path):
                    stem = os.path.splitext(name)[0]
                    editor.setSelection(0, len(stem) if stem else len(name))
                else:
                    editor.selectAll()

        QTimer.singleShot(0, select_without_extension)
        self.status_label.setText("이름 편집 중... (Enter: 확인 / Esc: 취소)")

    def _on_inline_rename_done(self, old_path, new_path):
        """인라인 이름 변경 완료 콜백 — undo 스택 등록, 즐겨찾기·상태바 업데이트"""
        # 즐겨찾기 경로 업데이트 (폴더인 경우)
        if os.path.isdir(new_path):
            self.update_favorite_path(old_path, new_path)

        # 실행 취소 스택에 추가
        self.undo_stack.append({
            'type': 'rename',
            'old_path': old_path,
            'new_path': new_path
        })

        # 현재 탭 새로고침 및 상태바 업데이트
        current_tab = self.active_tab_widget.currentWidget()
        if current_tab:
            self.refresh_view(current_tab)
        self.status_label.setText("이름 변경 완료")

    def edit_memo(self):
        """선택된 파일/폴더의 메모 편집 (F3)"""
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        selected = current_tab.tree.selectionModel().selectedIndexes()
        if not selected or len([idx for idx in selected if idx.column() == 0]) != 1:
            QMessageBox.warning(self, "경고", "항목을 하나만 선택하세요.")
            return
        
        # 선택된 행의 메모 컬럼 인덱스 찾기
        selected_row_index = None
        for idx in selected:
            if idx.column() == 0:
                selected_row_index = idx
                break
        
        if selected_row_index:
            # 메모 컬럼 (4번 컬럼)의 인덱스로 편집 시작
            memo_index = current_tab.model.index(selected_row_index.row(), 4, selected_row_index.parent())
            current_tab.tree.setCurrentIndex(memo_index)
            current_tab.tree.edit(memo_index)
            self.status_label.setText("메모 편집 중...")
    
    def undo_last_operation(self):
        """마지막 작업 실행 취소"""
        if not self.undo_stack:
            QMessageBox.information(self, "알림", "되돌릴 작업이 없습니다.")
            return
        
        # 마지막 작업 가져오기
        last_operation = self.undo_stack.pop()
        operation_type = last_operation.get('type')
        
        try:
            if operation_type == 'rename':
                # 이름 변경 취소: 새 이름을 원래 이름으로 되돌림
                old_path = last_operation['old_path']
                new_path = last_operation['new_path']
                
                if os.path.exists(new_path):
                    os.rename(new_path, old_path)
                    # 메모도 되돌림
                    current_tab = self.active_tab_widget.currentWidget()
                    if current_tab and hasattr(current_tab, 'source_model'):
                        current_tab.source_model.move_memo(new_path, old_path)
                    self.status_label.setText(f"이름 변경 취소: {os.path.basename(old_path)}")
                else:
                    QMessageBox.warning(self, "오류", "원본 파일을 찾을 수 없습니다.")
                    return
            
            elif operation_type == 'move':
                # 이동 취소: 새 위치에서 원래 위치로 되돌림
                old_path = last_operation['old_path']
                new_path = last_operation['new_path']
                
                if os.path.exists(new_path):
                    shutil.move(new_path, old_path)
                    # 메모도 되돌림
                    current_tab = self.active_tab_widget.currentWidget()
                    if current_tab and hasattr(current_tab, 'source_model'):
                        current_tab.source_model.move_memo(new_path, old_path)
                    self.status_label.setText(f"이동 취소: {os.path.basename(old_path)}")
                else:
                    QMessageBox.warning(self, "오류", "이동된 파일을 찾을 수 없습니다.")
                    return
            
            elif operation_type == 'copy':
                # 복사 취소: 복사된 파일/폴더 삭제
                created_path = last_operation['created_path']
                
                if os.path.exists(created_path):
                    # 휴지통으로 보내기
                    try:
                        send2trash(created_path)
                        self.status_label.setText(f"복사 취소: {os.path.basename(created_path)}")
                    except Exception as e:
                        # 휴지통 실패 시 직접 삭제
                        if os.path.isdir(created_path):
                            shutil.rmtree(created_path)
                        else:
                            os.remove(created_path)
                        self.status_label.setText(f"복사 취소 (영구 삭제): {os.path.basename(created_path)}")
                else:
                    QMessageBox.warning(self, "오류", "복사된 파일을 찾을 수 없습니다.")
                    return
            
            # 모든 탭의 뷰 새로고침
            current_tab = self.active_tab_widget.currentWidget()
            if current_tab:
                self.refresh_view(current_tab)
            
        except Exception as e:
            QMessageBox.warning(self, "오류", f"작업 취소 실패\n{e}")
            # 실패한 경우 스택에 다시 추가
            self.undo_stack.append(last_operation)
    
    def create_new_folder(self):
        """새 폴더 만들기"""
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        # 커스텀 다이얼로그로 더 큰 입력창 제공
        dialog = QInputDialog(self)
        dialog.setWindowTitle("새 폴더")
        dialog.setLabelText("폴더 이름:")
        dialog.setTextValue("새 폴더")
        dialog.resize(400, 150)  # 크기 조정
        
        # 다이얼로그 스타일 설정
        dialog.setStyleSheet("""
            QInputDialog {
                background-color: #f0f4f8;
            }
            QLineEdit {
                background-color: #ffffff;
                color: #1a365d;
                border: 1px solid #b8cfe0;
                padding: 5px 10px;
                border-radius: 4px;
                font-size: 9pt;
                selection-background-color: #4a90e2;
                selection-color: #ffffff;
            }
            QLineEdit:focus {
                border: 2px solid #4a90e2;
            }
            QLabel {
                color: #2c5282;
                font-size: 9pt;
            }
            QPushButton {
                background-color: #e8f0f7;
                color: #2c5282;
                border: 1px solid #b8cfe0;
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #d6e9f7;
                border-color: #4a90e2;
            }
        """)
        
        ok = dialog.exec()
        name = dialog.textValue()
        
        if ok and name:
            new_path = os.path.join(current_tab.current_path, name)
            try:
                os.makedirs(new_path)
                self.refresh_view(current_tab)
                self.status_label.setText("폴더 생성 완료")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"폴더 생성 실패\n{e}")
    
    def create_subfolder_in_path(self, parent_path, tab):
        """지정된 경로 안에 새 폴더 만들기 (하위 폴더 생성)"""
        if not os.path.isdir(parent_path):
            QMessageBox.warning(self, "오류", "선택한 항목이 폴더가 아닙니다.")
            return
        
        # 커스텀 다이얼로그로 더 큰 입력창 제공
        dialog = QInputDialog(self)
        dialog.setWindowTitle("하위 새폴더 생성")
        dialog.setLabelText(f"폴더 이름:\n(생성 위치: {parent_path})")
        dialog.setTextValue("새 폴더")
        dialog.resize(500, 180)  # 크기 조정
        dialog.setStyleSheet("""
            QLineEdit {
                selection-background-color: #4a90e2;
                selection-color: #ffffff;
            }
        """)
        
        ok = dialog.exec()
        name = dialog.textValue()
        
        if ok and name:
            new_path = os.path.join(parent_path, name)
            try:
                os.makedirs(new_path)
                
                # 현재 경로 유지 (자동 이동하지 않음)
                self.refresh_view(tab)
                self.status_label.setText(f"하위 폴더 생성 완료: {new_path}")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"폴더 생성 실패\n{e}")

    def create_same_folder(self, selected_folder_path, tab):
        """선택된 폴더와 동일한 이름의 폴더를 선택된 폴더의 부모 폴더에 생성"""
        if not os.path.isdir(selected_folder_path):
            QMessageBox.warning(self, "오류", "선택한 항목이 폴더가 아닙니다.")
            return

        parent_dir = os.path.dirname(selected_folder_path)
        default_name = os.path.basename(selected_folder_path)

        dialog = QInputDialog(self)
        dialog.setWindowTitle("동일 폴더 생성")
        dialog.setLabelText(f"폴더 이름:\n(생성 위치: {parent_dir})")
        dialog.setTextValue(default_name)
        dialog.resize(500, 180)
        dialog.setStyleSheet("""
            QLineEdit {
                selection-background-color: #4a90e2;
                selection-color: #ffffff;
            }
        """)

        ok = dialog.exec()
        name = dialog.textValue()

        if ok and name:
            new_path = os.path.join(parent_dir, name)
            if os.path.exists(new_path):
                base = new_path
                counter = 1
                while os.path.exists(f"{base} ({counter})"):
                    counter += 1
                new_path = f"{base} ({counter})"

            try:
                os.makedirs(new_path)
                self.refresh_view(tab)
                self.status_label.setText(f"동일 폴더 생성 완료: {new_path}")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"폴더 생성 실패\n{e}")
    
    def compress_to_zip(self, file_paths, tab):
        """여러 파일/폴더를 ZIP으로 압축"""
        if not file_paths:
            return
        
        # 압축 파일 이름 입력받기
        parent_dir = os.path.dirname(file_paths[0])
        
        # 파일명을 기준으로 정렬하여 가장 빠른 이름 가져오기
        sorted_paths = sorted(file_paths, key=lambda x: os.path.basename(x).lower())
        first_name = os.path.splitext(os.path.basename(sorted_paths[0]))[0]
        
        dialog = QInputDialog(self)
        dialog.setWindowTitle("ZIP 압축")
        dialog.setLabelText("압축 파일 이름 (확장자 제외):")
        dialog.setTextValue(first_name)
        dialog.resize(500, 180)
        
        ok = dialog.exec()
        zip_name = dialog.textValue()
        
        if not ok or not zip_name:
            return
        
        # .zip 확장자 추가
        if not zip_name.lower().endswith('.zip'):
            zip_name += '.zip'
        
        zip_path = os.path.join(parent_dir, zip_name)
        
        # 동일 이름 파일이 있으면 번호 추가
        if os.path.exists(zip_path):
            base = zip_path[:-4]  # .zip 제거
            counter = 1
            while os.path.exists(f"{base} ({counter}).zip"):
                counter += 1
            zip_path = f"{base} ({counter}).zip"
        
        try:
            progress = QProgressDialog("압축 중...", "취소", 0, len(file_paths), self)
            progress.setWindowModality(Qt.WindowModal)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for i, file_path in enumerate(file_paths):
                    if progress.wasCanceled():
                        # 취소 시 생성된 zip 파일 삭제
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                        return
                    
                    progress.setValue(i)
                    progress.setLabelText(f"압축 중: {os.path.basename(file_path)}")
                    
                    if os.path.isfile(file_path):
                        # 파일인 경우
                        zipf.write(file_path, os.path.basename(file_path))
                    elif os.path.isdir(file_path):
                        # 폴더인 경우 - 하위 파일들도 모두 압축
                        for root, dirs, files in os.walk(file_path):
                            for file in files:
                                file_full_path = os.path.join(root, file)
                                # 아카이브 내 경로 계산
                                arcname = os.path.join(
                                    os.path.basename(file_path),
                                    os.path.relpath(file_full_path, file_path)
                                )
                                zipf.write(file_full_path, arcname)
            
            progress.setValue(len(file_paths))
            self.refresh_view(tab)
            self.status_label.setText(f"압축 완료: {zip_path}")
            QMessageBox.information(self, "완료", f"압축이 완료되었습니다.\n{zip_path}")
            
        except Exception as e:
            QMessageBox.warning(self, "오류", f"압축 실패\n{e}")
    
    def extract_zip_here(self, zip_path, tab):
        """ZIP 파일을 현재 폴더에 압축 해제"""
        if not os.path.exists(zip_path):
            QMessageBox.warning(self, "오류", "파일을 찾을 수 없습니다.")
            return
        
        extract_dir = os.path.dirname(zip_path)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                file_list = zipf.namelist()
                
                progress = QProgressDialog("압축 해제 중...", "취소", 0, len(file_list), self)
                progress.setWindowModality(Qt.WindowModal)
                
                for i, file in enumerate(file_list):
                    if progress.wasCanceled():
                        break
                    
                    progress.setValue(i)
                    progress.setLabelText(f"압축 해제 중: {file}")
                    zipf.extract(file, extract_dir)
                
                progress.setValue(len(file_list))
            
            self.refresh_view(tab)
            self.status_label.setText(f"압축 해제 완료: {extract_dir}")
            QMessageBox.information(self, "완료", f"압축 해제가 완료되었습니다.\n위치: {extract_dir}")
            
        except Exception as e:
            QMessageBox.warning(self, "오류", f"압축 해제 실패\n{e}")
    
    def extract_zip_to_folder(self, zip_path, tab):
        """ZIP 파일 이름의 폴더를 생성하여 그 안에 압축 해제"""
        if not os.path.exists(zip_path):
            QMessageBox.warning(self, "오류", "파일을 찾을 수 없습니다.")
            return
        
        # zip 파일 이름에서 확장자 제거한 폴더명 생성
        parent_dir = os.path.dirname(zip_path)
        folder_name = os.path.splitext(os.path.basename(zip_path))[0]
        extract_dir = os.path.join(parent_dir, folder_name)
        
        # 동일 이름 폴더가 있으면 번호 추가
        if os.path.exists(extract_dir):
            counter = 1
            while os.path.exists(f"{extract_dir} ({counter})"):
                counter += 1
            extract_dir = f"{extract_dir} ({counter})"
        
        try:
            os.makedirs(extract_dir, exist_ok=True)
            
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                file_list = zipf.namelist()
                
                progress = QProgressDialog("압축 해제 중...", "취소", 0, len(file_list), self)
                progress.setWindowModality(Qt.WindowModal)
                
                for i, file in enumerate(file_list):
                    if progress.wasCanceled():
                        break
                    
                    progress.setValue(i)
                    progress.setLabelText(f"압축 해제 중: {file}")
                    zipf.extract(file, extract_dir)
                
                progress.setValue(len(file_list))
            
            self.refresh_view(tab)
            self.status_label.setText(f"압축 해제 완료: {extract_dir}")
            QMessageBox.information(self, "완료", f"압축 해제가 완료되었습니다.\n위치: {extract_dir}")
            
        except Exception as e:
            QMessageBox.warning(self, "오류", f"압축 해제 실패\n{e}")
    
    def empty_folder(self, folder_path, tab):
        """폴더 내부의 모든 파일과 하위 폴더 삭제"""
        if not os.path.isdir(folder_path):
            QMessageBox.warning(self, "오류", "선택한 항목이 폴더가 아닙니다.")
            return
        
        # 폴더 내 항목 목록 가져오기
        try:
            items = os.listdir(folder_path)
        except Exception as e:
            QMessageBox.warning(self, "오류", f"폴더 내용을 읽을 수 없습니다.\n{e}")
            return
        
        if not items:
            QMessageBox.information(self, "알림", "폴더가 이미 비어있습니다.")
            return
        
        # 확인 메시지
        reply = QMessageBox.question(
            self, 
            "폴더 비우기 확인",
            f"'{folder_path}' 폴더의 {len(items)}개 항목을 휴지통으로 이동하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 항목 삭제 처리
        failed_items = []
        success_count = 0
        
        for item in items:
            item_path = os.path.join(folder_path, item)
            try:
                # 경로 정규화
                normalized_path = os.path.normpath(item_path)
                
                # 파일/폴더 존재 확인
                if not os.path.exists(normalized_path):
                    failed_items.append(f"{item}\n(존재하지 않음)")
                    continue
                
                # 휴지통으로 이동
                send2trash(normalized_path)
                success_count += 1
                
            except Exception as e:
                failed_items.append(f"{item}\n({str(e)})")
        
        # 결과 메시지
        if success_count > 0:
            self.refresh_view(tab)
            self.status_label.setText(f"폴더 비우기 완료: {success_count}개 항목 삭제됨")
        
        if failed_items:
            error_msg = f"삭제 실패한 항목 ({len(failed_items)}개):\n\n"
            error_msg += "\n\n".join(failed_items[:5])  # 최대 5개만 표시
            if len(failed_items) > 5:
                error_msg += f"\n\n... 외 {len(failed_items) - 5}개"
            QMessageBox.warning(self, "삭제 오류", error_msg)
        elif success_count > 0:
            # 모두 성공한 경우만 완료 메시지 표시
            QMessageBox.information(self, "완료", f"폴더가 비워졌습니다.\n{success_count}개 항목이 휴지통으로 이동되었습니다.")
    
    def show_properties(self, tab):
        """파일 속성 표시"""
        selected = tab.tree.selectionModel().selectedIndexes()
        if selected:
            # 프록시 인덱스를 소스 인덱스로 변환
            source_index = tab.model.mapToSource(selected[0])
            path = tab.source_model.filePath(source_index)
            dialog = FilePropertiesDialog(path, self)
            dialog.exec()
    
    def on_favorite_clicked(self, item):
        """즐겨찾기 클릭"""
        text = item.text()
        
        # 경로 매핑에서 실제 경로 가져오기
        path = self.favorite_paths.get(text)
        
        if path and os.path.exists(path):
            current_tab = self.active_tab_widget.currentWidget()
            if current_tab:
                self.navigate_to(current_tab, path, self.active_side)
        else:
            QMessageBox.warning(self, "경고", f"경로를 찾을 수 없습니다:\n{text}")
    
    def on_drive_clicked(self, item):
        """드라이브 클릭"""
        drive = item.text().replace("💿 ", "")
        if os.path.exists(drive):
            current_tab = self.active_tab_widget.currentWidget()
            if current_tab:
                self.navigate_to(current_tab, drive, self.active_side)
    
    def show_favorites_context_menu(self, pos):
        """즐겨찾기 컨텍스트 메뉴"""
        menu = QMenu(self)
        
        item = self.favorites.itemAt(pos)
        if item:
            text = item.text()
            path = self.favorite_paths.get(text)
            
            # 경로 복사
            if path:
                copy_path_action = QAction("경로 복사", self)
                copy_path_action.triggered.connect(lambda: self.copy_path_to_clipboard_simple(path))
                menu.addAction(copy_path_action)
                menu.addSeparator()
            
            # 사용자 추가 즐겨찾기만 제거 가능
            if not text.startswith(("🏠", "💼", "🖼️", "⬇️")):
                remove_action = QAction("즐겨찾기에서 제거", self)
                remove_action.triggered.connect(lambda: self.remove_favorite(item))
                menu.addAction(remove_action)
        
        menu.exec_(self.favorites.viewport().mapToGlobal(pos))
    
    def remove_favorite(self, item):
        """즐겨찾기 제거"""
        text = item.text()
        # 경로 매핑에서도 제거
        if text in self.favorite_paths:
            del self.favorite_paths[text]
        # 리스트에서 제거
        self.favorites.takeItem(self.favorites.row(item))
        self.save_persisted_favorites()
        self.status_label.setText("즐겨찾기에서 제거됨")
    
    def add_favorite_path(self, path):
        """지정한 경로를 즐겨찾기에 추가"""
        if not isinstance(path, str):
            return
        path = self.normalize_path(path)
        name = os.path.basename(path) or path
        item_text = f"📁 {name}"

        normalized_existing = [self.normalize_path(p) for p in self.favorite_paths.values()]
        if path in normalized_existing:
            QMessageBox.warning(self, "경고", "이 폴더는 이미 즐겨찾기에 추가되어 있습니다.")
            return

        self.favorites.addItem(item_text)
        self.favorite_paths[item_text] = path
        self.save_persisted_favorites()
        self.status_label.setText(f"즐겨찾기에 추가됨: {name}")

    def update_favorite_path(self, old_path, new_path):
        """
        즐겨찾기 경로 업데이트 (폴더 이름 변경 시)
        [프로세스]
        1. 즐겨찾기 목록에서 old_path와 일치하는 항목 찾기
        2. 경로를 new_path로 업데이트
        3. 즐겨찾기 이름도 새 폴더 이름으로 업데이트
        4. 하위 경로도 함께 업데이트
        """
        old_path_normalized = self.normalize_path(old_path)
        new_path_normalized = self.normalize_path(new_path)
        updated = False
        
        # 즐겨찾기 목록에서 업데이트할 항목 찾기
        items_to_update = []
        for i in range(self.favorites.count()):
            item = self.favorites.item(i)
            text = item.text()
            
            # 기본 즐겨찾기는 건너뛰기
            if self._is_builtin_favorite_text(text):
                continue
            
            path = self.favorite_paths.get(text)
            if not path:
                continue
            
            path_normalized = self.normalize_path(path)
            
            # 정확히 일치하거나 하위 경로인 경우
            if path_normalized == old_path_normalized:
                items_to_update.append((i, text, new_path_normalized, True))  # 정확히 일치
                updated = True
            elif path_normalized.startswith(old_path_normalized + "\\"):
                # 하위 경로인 경우 경로 업데이트
                relative_part = path_normalized[len(old_path_normalized):]
                new_full_path = new_path_normalized + relative_part
                items_to_update.append((i, text, new_full_path, False))  # 하위 경로
                updated = True
        
        # 업데이트 적용
        for index, old_text, updated_path, is_exact_match in items_to_update:
            item = self.favorites.item(index)
            
            if is_exact_match:
                # 정확히 일치하는 경우: 이름도 변경
                new_name = os.path.basename(new_path)
                item.setText(new_name)
                # 딕셔너리 키도 업데이트
                del self.favorite_paths[old_text]
                self.favorite_paths[new_name] = updated_path
            else:
                # 하위 경로인 경우: 경로만 업데이트
                self.favorite_paths[old_text] = updated_path
        
        # 변경사항 저장
        if updated:
            self.save_persisted_favorites()
            self.status_label.setText("즐겨찾기 경로 업데이트 완료")
    
    def add_favorite(self):
        """즐겨찾기 추가"""
        current_tab = self.active_tab_widget.currentWidget()
        if current_tab:
            self.add_favorite_path(current_tab.current_path)

    def closeEvent(self, event):
        """프로그램 종료 시 모든 데이터 저장 (즐겨찾기/탭/컬럼순서/메모)"""
        try:
            if hasattr(self, "favorites") and self.favorites is not None:
                self.save_persisted_favorites()
            self.save_persisted_tabs()
            self.save_column_order()  # 컬럼 순서 저장
            self.save_memos()  # 메모 저장
        finally:
            super().closeEvent(event)
    
    def add_plus_tab(self, tab_widget, side):
        """+ 탭 추가 (더미 탭) - 이미 존재하면 추가하지 않음"""
        # 이미 + 탭이 있는지 확인
        for i in range(tab_widget.count()):
            widget = tab_widget.widget(i)
            if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                return  # 이미 존재하면 추가하지 않음
        
        # 빈 위젯 생성
        plus_widget = QWidget()
        layout = QVBoxLayout(plus_widget)
        label = QLabel("새 탭을 추가하려면 이 탭을 클릭하세요")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        
        # + 탭 추가
        index = tab_widget.addTab(plus_widget, "+")
        
        # + 탭의 닫기 버튼 숨기기 - None 대신 빈 QWidget 사용
        tab_bar = tab_widget.tabBar()
        from PySide6.QtWidgets import QTabBar
        # 빈 위젯을 생성하여 닫기 버튼 위치에 배치 (투명하게)
        empty_widget = QWidget()
        empty_widget.setFixedSize(0, 0)  # 크기를 0으로 설정
        tab_bar.setTabButton(index, QTabBar.RightSide, empty_widget)
        
        # + 탭의 속성 저장
        plus_widget.is_plus_tab = True
        plus_widget.tab_side = side
    
    def on_left_tab_changed(self, index):
        """왼쪽 탭 변경"""
        if index >= 0:
            self.active_tab_widget = self.left_tabs
            self.active_side = 'left'
            self.update_active_panel_highlight()
            
            # 닫은 탭으로 인해 + 탭이 자동으로 선택되는 경우 무시
            if self.ignore_plus_activation['left']:
                self.ignore_plus_activation['left'] = False
                # + 탭이 자동으로 선택된 상태로 남으면, 다시 클릭해도 currentChanged가 발생하지 않아
                # 새 탭이 만들어지지 않을 수 있으므로 실제 탭으로 이동시킨다.
                widget = self.left_tabs.widget(index)
                if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                    for i in range(self.left_tabs.count()):
                        w = self.left_tabs.widget(i)
                        if not (hasattr(w, 'is_plus_tab') and w.is_plus_tab):
                            self.left_tabs.setCurrentIndex(i)
                            break
                return
            
            # + 탭을 클릭한 경우
            widget = self.left_tabs.widget(index)
            if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                # + 탭을 제거하고 새 탭 추가 (내부에서 + 탭을 다시 추가하므로 여기서는 하지 않음)
                self.left_tabs.removeTab(index)
                self.add_new_tab_to_widget(self.left_tabs, 'left')
    
    def on_right_tab_changed(self, index):
        """오른쪽 탭 변경"""
        if index >= 0:
            self.active_tab_widget = self.right_tabs
            self.active_side = 'right'
            self.update_active_panel_highlight()
            
            # 닫은 탭으로 인해 + 탭이 자동으로 선택되는 경우 무시
            if self.ignore_plus_activation['right']:
                self.ignore_plus_activation['right'] = False
                # + 탭이 자동으로 선택된 상태로 남으면, 다시 클릭해도 currentChanged가 발생하지 않아
                # 새 탭이 만들어지지 않을 수 있으므로 실제 탭으로 이동시킨다.
                widget = self.right_tabs.widget(index)
                if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                    for i in range(self.right_tabs.count()):
                        w = self.right_tabs.widget(i)
                        if not (hasattr(w, 'is_plus_tab') and w.is_plus_tab):
                            self.right_tabs.setCurrentIndex(i)
                            break
                return
            
            # + 탭을 클릭한 경우
            widget = self.right_tabs.widget(index)
            if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                # + 탭을 제거하고 새 탭 추가 (내부에서 + 탭을 다시 추가하므로 여기서는 하지 않음)
                self.right_tabs.removeTab(index)
                self.add_new_tab_to_widget(self.right_tabs, 'right')
    
    def update_active_panel_highlight(self):
        """
        현재 활성화된 패널(left/right)을 시각적으로 하이라이트 표시
        - 활성 패널: 탭바 상단에 파란색 강조 테두리 표시
        - 비활성 패널: 기본 스타일 유지
        """
        is_dark = getattr(self, 'current_theme', 'light') == 'dark'

        if is_dark:
            # ── 다크 테마: 활성 패널 ──────────────────────────────────────
            active_style = """
                QTabWidget::pane {
                    border: 2px solid #4a90e2;
                    border-top: 3px solid #5ba3f5;
                    background-color: #14161e;
                    border-radius: 4px;
                }
                QTabBar::tab {
                    background-color: #252830;
                    color: #b8cce0;
                    padding: 6px 14px;
                    margin-right: 3px;
                    border: 1px solid #3a4050;
                    border-bottom: none;
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                    font-size: 9pt;
                }
                QTabBar::tab:selected {
                    background-color: #14161e;
                    color: #e8f0ff;
                    border: 1px solid #4a90e2;
                    border-bottom: 2px solid #14161e;
                    font-weight: bold;
                }
                QTabBar::tab:hover:!selected {
                    background-color: #2e3448;
                    color: #d0e0f4;
                }
            """
            # ── 다크 테마: 비활성 패널 ────────────────────────────────────
            inactive_style = """
                QTabWidget::pane {
                    border: 1px solid #2e3340;
                    background-color: #14161e;
                    border-radius: 4px;
                }
                QTabBar::tab {
                    background-color: #1c1f28;
                    color: #7a90a8;
                    padding: 6px 14px;
                    margin-right: 3px;
                    border: 1px solid #2a3040;
                    border-bottom: none;
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                    font-size: 9pt;
                }
                QTabBar::tab:selected {
                    background-color: #14161e;
                    color: #9ab0c8;
                    border: 1px solid #3a4050;
                    border-bottom: 2px solid #3a4a60;
                }
                QTabBar::tab:hover:!selected {
                    background-color: #22273a;
                    color: #90a8c0;
                }
            """
        else:
            # ── 라이트 테마: 활성 패널 ────────────────────────────────────
            active_style = """
                QTabWidget::pane {
                    border: 2px solid #4a90e2;
                    border-top: 3px solid #4a90e2;
                    background-color: #ffffff;
                    border-radius: 4px;
                }
                QTabBar::tab {
                    background-color: #dce8f0;
                    color: #2c5282;
                    padding: 6px 12px;
                    margin-right: 2px;
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                    border: 1px solid #b8cfe0;
                    border-bottom: none;
                    font-size: 9pt;
                }
                QTabBar::tab:selected {
                    background-color: #ffffff;
                    color: #1a365d;
                    border-bottom: 2px solid #4a90e2;
                    font-weight: bold;
                }
                QTabBar::tab:hover:!selected { background-color: #e6f0f7; }
            """
            # ── 라이트 테마: 비활성 패널 ──────────────────────────────────
            inactive_style = """
                QTabWidget::pane {
                    border: 1px solid #cbd5e0;
                    background-color: #f7fafc;
                    border-radius: 4px;
                }
                QTabBar::tab {
                    background-color: #edf2f7;
                    color: #718096;
                    padding: 6px 12px;
                    margin-right: 2px;
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                    border: 1px solid #cbd5e0;
                    border-bottom: none;
                    font-size: 9pt;
                }
                QTabBar::tab:selected {
                    background-color: #f7fafc;
                    color: #4a5568;
                    border-bottom: 2px solid #a0aec0;
                }
                QTabBar::tab:hover:!selected { background-color: #e2e8f0; }
            """

        if self.active_side == 'left':
            self.left_tabs.setStyleSheet(active_style)
            self.right_tabs.setStyleSheet(inactive_style)
        else:
            self.left_tabs.setStyleSheet(inactive_style)
            self.right_tabs.setStyleSheet(active_style)
    
    def close_tab(self, tab_widget, index, side):
        """탭 닫기"""
        # 인덱스가 유효한지 확인
        if index < 0 or index >= tab_widget.count():
            return
        
        # + 탭은 닫을 수 없음
        widget = tab_widget.widget(index)
        if widget is None:
            return
        
        if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
            return
        
        # + 탭을 제외한 실제 탭이 1개 이상인 경우에만 닫기 허용
        real_tab_count = sum(1 for i in range(tab_widget.count()) 
                            if not (hasattr(tab_widget.widget(i), 'is_plus_tab') and tab_widget.widget(i).is_plus_tab))
        
        if real_tab_count > 1:
            if tab_widget.currentIndex() == index:
                self.ignore_plus_activation[side] = True
            tab_widget.removeTab(index)
            self.add_plus_tab(tab_widget, side)
    
    def create_new_tab(self):
        """새 탭 추가 (단축키용)"""
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            # 현재 탭이 없으면 홈 디렉토리 사용
            path = os.path.expanduser("~")
        else:
            # 현재 탭의 경로 사용
            path = current_tab.current_path
        
        # + 탭 제거
        self.remove_plus_tab(self.active_tab_widget)
        
        # 자동으로 이름 생성
        self.add_tab(self.active_tab_widget, path, None, self.active_side)
        
        # + 탭 다시 추가
        self.add_plus_tab(self.active_tab_widget, self.active_side)
        
        # 새로 추가된 탭으로 전환 (+ 탭 제외한 마지막 탭)
        self.active_tab_widget.setCurrentIndex(self.active_tab_widget.count() - 2)
        name = os.path.basename(path) or path
        self.status_label.setText(f"새 탭 추가됨: {name}")
    
    def move_tab_between_sides(self, source_side, source_index, target_side):
        """탭을 한쪽에서 다른 쪽으로 이동"""
        # 소스와 타겟 탭 위젯 가져오기
        source_widget = self.left_tabs if source_side == 'left' else self.right_tabs
        target_widget = self.left_tabs if target_side == 'left' else self.right_tabs
        
        # 같은 위젯이면 무시
        if source_widget == target_widget:
            return
        
        # 소스 탭 정보 가져오기
        if source_index < 0 or source_index >= source_widget.count():
            return
        
        tab = source_widget.widget(source_index)
        
        # + 탭은 이동 불가
        if tab and hasattr(tab, 'is_plus_tab') and tab.is_plus_tab:
            return
        
        tab_text = source_widget.tabText(source_index)
        
        # 탭의 현재 경로 가져오기
        if not hasattr(tab, 'current_path'):
            return
        
        current_path = tab.current_path
        
        # 소스에서 + 탭 제거
        self.remove_plus_tab(source_widget)
        
        # 타겟에서 + 탭 제거
        self.remove_plus_tab(target_widget)
        
        # 소스에서 탭 제거
        source_widget.removeTab(source_index)
        
        # 타겟에 탭 추가
        self.add_tab(target_widget, current_path, tab_text, target_side)
        
        # + 탭 다시 추가
        self.add_plus_tab(source_widget, source_side)
        self.add_plus_tab(target_widget, target_side)
        
        # 새로 추가된 탭을 활성화 (+ 탭 제외한 마지막 탭)
        for i in range(target_widget.count() - 1, -1, -1):
            widget = target_widget.widget(i)
            if not (hasattr(widget, 'is_plus_tab') and widget.is_plus_tab):
                target_widget.setCurrentIndex(i)
                break
        
        # 활성 탭 위젯 업데이트
        self.active_tab_widget = target_widget
        self.active_side = target_side
        
        self.status_label.setText(f"탭을 {source_side}에서 {target_side}로 이동했습니다")
    
    def add_new_tab_to_widget(self, tab_widget, side):
        """+ 버튼으로 새 탭 추가"""
        # 활성 탭 위젯과 사이드 업데이트
        self.active_tab_widget = tab_widget
        self.active_side = side
        
        # 현재 탭의 경로 사용 (없으면 홈 디렉토리)
        current_tab = tab_widget.currentWidget()
        if current_tab and not (hasattr(current_tab, 'is_plus_tab') and current_tab.is_plus_tab):
            path = current_tab.current_path
        else:
            path = os.path.expanduser("~")
        
        # + 탭 제거
        self.remove_plus_tab(tab_widget)
        
        # 자동으로 이름 생성하여 탭 추가
        self.add_tab(tab_widget, path, None, side)
        
        # + 탭 다시 추가
        self.add_plus_tab(tab_widget, side)
        
        # 새로 추가된 탭으로 전환 (+ 탭 제외한 마지막 탭)
        tab_widget.setCurrentIndex(tab_widget.count() - 2)
        name = os.path.basename(path) or path
        self.status_label.setText(f"새 탭 추가됨: {name}")
    
    def remove_plus_tab(self, tab_widget):
        """+ 탭 제거 (존재하는 경우에만)"""
        for i in range(tab_widget.count() - 1, -1, -1):  # 뒤에서부터 순회
            widget = tab_widget.widget(i)
            if hasattr(widget, 'is_plus_tab') and widget.is_plus_tab:
                tab_widget.removeTab(i)
                return  # 하나만 제거하고 종료
    
    def show_tab_context_menu(self, tab_widget, pos, side):
        """탭 컨텍스트 메뉴 표시"""
        menu = QMenu(self)
        
        # 새 탭
        new_tab_action = QAction("➕ 새 탭", self)
        new_tab_action.triggered.connect(self.create_new_tab)
        menu.addAction(new_tab_action)
        
        # 클릭한 탭의 인덱스 가져오기
        tab_index = tab_widget.tabBar().tabAt(pos)
        
        if tab_index >= 0:
            menu.addSeparator()
            
            # 탭 닫기
            close_action = QAction("❌ 탭 닫기", self)
            close_action.triggered.connect(lambda: self.close_tab(tab_widget, tab_index, side))
            close_action.setEnabled(tab_widget.count() > 1)  # 마지막 탭은 닫을 수 없음
            menu.addAction(close_action)
            
            # 다른 탭 모두 닫기
            close_others_action = QAction("다른 탭 모두 닫기", self)
            close_others_action.triggered.connect(lambda: self.close_other_tabs(tab_widget, tab_index))
            close_others_action.setEnabled(tab_widget.count() > 1)
            menu.addAction(close_others_action)
            
            menu.addSeparator()
            
            # 탭 이름 변경
            rename_tab_action = QAction("✏️ 탭 이름 변경", self)
            rename_tab_action.triggered.connect(lambda: self.rename_tab(tab_widget, tab_index))
            menu.addAction(rename_tab_action)
        
        menu.exec_(tab_widget.tabBar().mapToGlobal(pos))
    
    def close_other_tabs(self, tab_widget, keep_index):
        """다른 탭 모두 닫기"""
        # 어느 쪽 탭인지 확인
        side = 'left' if tab_widget == self.left_tabs else 'right'
        
        # + 탭 제거
        self.remove_plus_tab(tab_widget)
        
        # 뒤에서부터 닫기 (인덱스 변경 방지)
        for i in range(tab_widget.count() - 1, -1, -1):
            widget = tab_widget.widget(i)
            # + 탭이 아니고, 유지할 탭이 아닌 경우에만 제거
            if i != keep_index and not (hasattr(widget, 'is_plus_tab') and widget.is_plus_tab):
                tab_widget.removeTab(i)

        # + 탭 다시 추가 (중복 체크)
        has_plus = any(
            hasattr(tab_widget.widget(i), 'is_plus_tab') and tab_widget.widget(i).is_plus_tab
            for i in range(tab_widget.count())
        )
        if not has_plus:
            side = 'left' if tab_widget == self.left_tabs else 'right'
            self.add_plus_tab(tab_widget, side)

    def rename_tab(self, tab_widget, index):
        """탭 이름 변경"""
        old_name = tab_widget.tabText(index)
        
        # 스타일이 적용된 다이얼로그 생성
        dialog = QInputDialog(self)
        dialog.setWindowTitle("탭 이름 변경")
        dialog.setLabelText("새 이름:")
        dialog.setTextValue(old_name)
        dialog.setStyleSheet("""
            QLineEdit {
                selection-background-color: #4a90e2;
                selection-color: #ffffff;
            }
        """)
        
        ok = dialog.exec()
        new_name = dialog.textValue()
        
        if ok and new_name:
            tab_widget.setTabText(index, new_name)
            self.status_label.setText(f"탭 이름 변경됨: {new_name}")

    def eventFilter(self, obj, event):
        """탭 바의 빈 공간 클릭도 활성 탭 사이드를 업데이트"""
        if event.type() == QEvent.MouseButtonPress:
            side = obj.property("tab_side") if obj is not None else None
            if side == 'left':
                self.active_tab_widget = self.left_tabs
                self.active_side = 'left'
            elif side == 'right':
                self.active_tab_widget = self.right_tabs
                self.active_side = 'right'
        return super().eventFilter(obj, event)
    
    def setup_shortcuts(self):
        """
        키보드 단축키 설정
        [단축키 목록]
        - Ctrl+C: 파일 복사
        - Ctrl+X: 파일 잘라내기
        - Ctrl+V: 파일 붙여넣기
        - Delete: 파일 삭제
        - F2: 이름 바꾸기
        - F3: 메모 편집
        - Ctrl+Shift+N: 새 폴더 만들기
        - Ctrl+T: 새 탭 열기
        - F5: 현재 폴더 새로고침
        - Ctrl+Z: 실행 취소
        """
        # Ctrl+C: 복사
        QShortcut(QKeySequence.Copy, self, self.copy_files)
        
        # Ctrl+X: 잘라내기
        QShortcut(QKeySequence.Cut, self, self.cut_files)
        
        # Ctrl+V: 붙여넣기
        QShortcut(QKeySequence.Paste, self, self.paste_files)
        
        # Delete: 삭제 (트리뷰에 포커스가 있을 때만 작동)
        QShortcut(QKeySequence.Delete, self, self.delete_files)
        
        # F2: 이름 바꾸기
        QShortcut(QKeySequence("F2"), self, self.rename_file)
        
        # F3: 메모 편집
        QShortcut(QKeySequence("F3"), self, self.edit_memo)
        
        # Ctrl+Shift+N: 새 폴더
        QShortcut(QKeySequence("Ctrl+Shift+N"), self, self.create_new_folder)
        
        # Ctrl+T: 새 탭
        QShortcut(QKeySequence("Ctrl+T"), self, self.create_new_tab)
        
        # F5: 새로고침
        QShortcut(QKeySequence.Refresh, self, self.handle_refresh_shortcut)
        
        # Ctrl+Z: 되돌아가기 (실행 취소)
        QShortcut(QKeySequence.Undo, self, self.undo_last_operation)
        
        # Ctrl+Shift+C: 전체 경로 복사
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.copy_full_path)

        # Ctrl+[: 모두 접기 (현재 활성 탭)
        QShortcut(QKeySequence("Ctrl+["), self, self.collapse_all)

        # Ctrl+]: 모두 펼치기 (현재 활성 탭)
        QShortcut(QKeySequence("Ctrl+]"), self, self.expand_all)
    
    def toggle_right_panel(self):
        """오른쪽 패널 표시/숨기기 토글"""
        is_visible = self.right_tabs.isVisible()
        new_state = not is_visible
        
        # 패널 표시/숨김
        self.right_tabs.setVisible(new_state)
        
        # Splitter 크기 조정
        if new_state:
            # 보이기: 이전에 저장된 크기가 있으면 복원, 없으면 50:50 기본값
            total_width = self.tabs_splitter.width()
            saved = getattr(self, '_saved_splitter_sizes', None)
            if saved and len(saved) == 2 and saved[1] > 0:
                self.tabs_splitter.setSizes(saved)
            else:
                self.tabs_splitter.setSizes([total_width // 2, total_width // 2])
            mode_text = "분할 패널 모드"
        else:
            # 숨기기 직전 현재 크기 저장
            self._saved_splitter_sizes = self.tabs_splitter.sizes()
            total_width = self.tabs_splitter.width()
            self.tabs_splitter.setSizes([total_width, 0])
            mode_text = "단일 패널 모드"
        
        # 메뉴 항목 체크 상태 업데이트
        if hasattr(self, 'toggle_right_panel_action'):
            self.toggle_right_panel_action.setChecked(new_state)
        
        # 토글 버튼 체크 상태 업데이트
        if hasattr(self, 'toggle_split_btn'):
            self.toggle_split_btn.setChecked(new_state)
        
        # 상태바 업데이트
        self.status_label.setText(mode_text)
        
        # 왼쪽 탭에 포커스 (오른쪽이 숨겨진 경우)
        if not new_state:
            self.active_tab_widget = self.left_tabs
            self.active_side = 'left'
    
    def handle_refresh_shortcut(self):
        """F5 단축키 처리 - 탐색기 탭이 활성화되어 있을 때만 새로고침"""
        # 현재 메인 탭이 탐색기 탭인지 확인
        current_main_tab_index = self.main_tabs.currentIndex()
        current_main_tab_text = self.main_tabs.tabText(current_main_tab_index)
        
        # 탐색기 탭이 활성화되어 있는 경우에만 새로고침
        if "탐색기" in current_main_tab_text:
            current_tab = self.active_tab_widget.currentWidget()
            if current_tab and hasattr(current_tab, 'tree'):
                # 새로고침 버튼이 있으면 애니메이션과 함께, 없으면 기본 새로고침
                if hasattr(current_tab, 'refresh_btn'):
                    self.refresh_view_with_animation(current_tab, current_tab.refresh_btn)
                else:
                    self.refresh_view(current_tab)
    
    def browse_search_path(self):
        """검색 경로 선택 대화상자"""
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "검색할 폴더 선택", self.search_path_input.text())
        if path:
            self.search_path_input.setText(path)
    
    def get_index_db_path(self):
        """인덱스 DB 파일 경로"""
        config_dir = self._config_dir()
        return config_dir / "file_index.db"
    
    def start_indexing(self):
        """인덱싱 시작"""
        # 인덱싱할 경로 목록 (주요 드라이브)
        index_paths = []
        
        if platform.system() == 'Windows':
            # Windows: C:, D: 드라이브
            for drive in ['C:\\', 'D:\\']:
                if os.path.exists(drive):
                    index_paths.append(drive)
        else:
            # Linux/Mac: 홈 디렉토리
            index_paths.append(os.path.expanduser("~"))
        
        if not index_paths:
            self.index_status_label.setText("인덱스: 인덱싱할 경로 없음")
            return
        
        # DB 경로 확인
        db_path = self.get_index_db_path()
        
        # 워커 생성
        self.index_worker = IndexWorker(str(db_path))
        self.index_worker.index_paths = index_paths
        
        # 시그널 연결
        self.index_worker.progress_update.connect(self.update_indexing_progress)
        self.index_worker.indexing_finished.connect(self.indexing_finished)
        
        # UI 업데이트
        self.index_status_label.setText("인덱스: 인덱싱 중...")
        self.index_status_label.setStyleSheet("font-weight: bold; color: #e67e22;")
        self.rebuild_index_btn.setEnabled(False)
        
        # 인덱싱 시작
        self.index_worker.start()
    
    def rebuild_index(self):
        """인덱스 재구축"""
        reply = QMessageBox.question(
            self,
            "인덱스 재구축",
            "전체 파일 시스템을 다시 인덱싱합니다.\n시간이 걸릴 수 있습니다. 계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.start_indexing()
    
    def update_indexing_progress(self, message, current, total):
        """인덱싱 진행 상황 업데이트"""
        if total > 0:
            progress_text = f"{message} ({current}%)"
        else:
            progress_text = message
        self.indexing_progress_label.setText(progress_text)
    
    def indexing_finished(self, count):
        """인덱싱 완료"""
        if count > 0:
            self.index_status_label.setText(f"인덱스: 준비 완료 ({count:,}개 파일)")
            self.index_status_label.setStyleSheet("font-weight: bold; color: #27ae60;")
            self.indexing_progress_label.setText(f"마지막 인덱싱: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            self.index_status_label.setText("인덱스: 인덱싱 실패")
            self.index_status_label.setStyleSheet("font-weight: bold; color: #c0392b;")
        
        self.rebuild_index_btn.setEnabled(True)
        self.status_label.setText(f"인덱싱 완료: {count:,}개 파일")
    
    def check_index_status(self):
        """인덱스 상태 확인"""
        db_path = self.get_index_db_path()
        
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), MAX(indexed_time) FROM file_index")
                count, last_indexed = cursor.fetchone()
                conn.close()
                
                if count and count > 0:
                    self.index_status_label.setText(f"인덱스: 준비 완료 ({count:,}개 파일)")
                    self.index_status_label.setStyleSheet("font-weight: bold; color: #27ae60;")
                    if last_indexed:
                        self.indexing_progress_label.setText(f"마지막 인덱싱: {last_indexed}")
                    self.rebuild_index_btn.setEnabled(True)
                    return True
            except:
                pass
        
        # 인덱스가 없으면 자동 시작
        self.index_status_label.setText("인덱스: 인덱싱 시작...")
        self.index_status_label.setStyleSheet("font-weight: bold; color: #e67e22;")
        self.start_indexing()
        return False
    
    def start_search(self):
        """검색 시작"""
        query = self.search_query_input.text().strip()
        if not query:
            QMessageBox.warning(self, "경고", "검색어를 입력하세요.")
            return
        
        # 선택된 드라이브 수집
        selected_drives = []
        for checkbox in self.drive_checkboxes:
            if checkbox.isChecked():
                drive_path = checkbox.property("drive_path")
                if drive_path and os.path.exists(drive_path):
                    selected_drives.append(drive_path)
        
        if not selected_drives:
            QMessageBox.warning(self, "경고", "검색할 드라이브를 하나 이상 선택하세요.")
            return
        
        # 기존 검색 중지
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.stop()
            self.search_worker.wait()
        
        # 결과 테이블 초기화
        self.search_results_table.setRowCount(0)
        
        # 워커 생성 및 설정
        self.search_worker = SearchWorker()
        self.search_worker.search_paths = selected_drives  # 여러 드라이브 설정
        self.search_worker.search_query = query
        self.search_worker.search_content = False  # 파일 내용 검색 비활성화
        self.search_worker.case_sensitive = not self.case_insensitive_check.isChecked()  # 대소문자 구분 여부
        self.search_worker.flexible_word_match = self.flexible_word_check.isChecked()  # 단어 순서 무시
        
        # 인덱스 사용 설정
        self.search_worker.use_index = self.use_index_check.isChecked()
        self.search_worker.db_path = str(self.get_index_db_path())
        
        # 파일 타입 필터
        file_type = self.file_type_combo.currentText()
        if file_type != "모든 파일":
            self.search_worker.file_type_filter = file_type
        
        # 크기 필터 및 날짜 필터 비활성화 (기본값)
        self.search_worker.min_size = 0
        self.search_worker.max_size = 0
        self.search_worker.date_filter = None
        
        # 시그널 연결
        self.search_worker.result_found.connect(self.add_search_result)
        self.search_worker.search_finished.connect(self.search_finished)
        self.search_worker.progress_update.connect(self.update_search_progress)
        
        # UI 업데이트
        self.search_start_btn.setEnabled(False)
        self.search_stop_btn.setEnabled(True)
        drive_names = ", ".join([os.path.basename(d.rstrip("\\")) or d for d in selected_drives])
        self.search_progress_label.setText(f"검색 시작 중... ({drive_names})")
        self.search_progress_label.setStyleSheet("padding: 5px; background: #fffacd; border-radius: 3px;")
        
        # 검색 시작
        self.search_worker.start()
        self.status_label.setText(f"검색 중: {query} ({len(selected_drives)}개 드라이브)")
    
    def stop_search(self):
        """검색 중지"""
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.stop()
            self.search_progress_label.setText("검색 중지 중...")
            self.search_progress_label.setStyleSheet("padding: 5px; background: #ffcccc; border-radius: 3px;")
    
    def add_search_result(self, result):
        """검색 결과를 테이블에 추가"""
        row = self.search_results_table.rowCount()
        self.search_results_table.insertRow(row)
        
        # 파일명
        name_item = QTableWidgetItem(result['name'])
        if result['is_dir']:
            name_item.setIcon(self.icon_provider.icon(QFileIconProvider.Folder))
        else:
            name_item.setIcon(self.icon_provider.icon(QFileIconProvider.File))
        self.search_results_table.setItem(row, 0, name_item)
        
        # 경로
        self.search_results_table.setItem(row, 1, QTableWidgetItem(result['dir']))
        
        # 크기
        size_text = self.format_size(result['size']) if not result['is_dir'] else "<폴더>"
        self.search_results_table.setItem(row, 2, QTableWidgetItem(size_text))
        
        # 수정 날짜
        self.search_results_table.setItem(row, 3, QTableWidgetItem(result['modified']))
        
        # 매칭 유형
        match_type = "내용" if result['match_type'] == 'content' else "파일명"
        self.search_results_table.setItem(row, 4, QTableWidgetItem(match_type))
        
        # 전체 경로를 데이터로 저장
        name_item.setData(Qt.UserRole, result['path'])
    
    def search_finished(self, count):
        """검색 완료"""
        self.search_start_btn.setEnabled(True)
        self.search_stop_btn.setEnabled(False)
        self.search_progress_label.setText(f"검색 완료: {count}개 항목 발견")
        self.search_progress_label.setStyleSheet("padding: 5px; background: #ccffcc; border-radius: 3px;")
        self.status_label.setText(f"검색 완료: {count}개 항목")
    
    def update_search_progress(self, message):
        """검색 진행 상황 업데이트"""
        self.search_progress_label.setText(message)
    
    def clear_search_results(self):
        """검색 결과 지우기"""
        self.search_results_table.setRowCount(0)
        self.search_progress_label.setText("대기 중...")
        self.search_progress_label.setStyleSheet("padding: 5px; background: #f0f0f0; border-radius: 3px;")
    
    def open_search_result(self, index):
        """검색 결과 더블클릭 - 파일/폴더 열기"""
        if not index.isValid():
            return
        
        row = index.row()
        name_item = self.search_results_table.item(row, 0)
        if name_item:
            file_path = name_item.data(Qt.UserRole)
            if file_path and os.path.exists(file_path):
                if os.path.isdir(file_path):
                    # 폴더인 경우 탐색기 탭에서 열기
                    self.main_tabs.setCurrentIndex(0)  # 탐색기 탭으로 전환
                    current_tab = self.active_tab_widget.currentWidget()
                    if current_tab and hasattr(current_tab, 'current_path'):
                        self.navigate_to(current_tab, file_path, self.active_side)
                else:
                    # 파일인 경우 실행
                    self.open_file(file_path)
    
    def show_search_result_context_menu(self, pos):
        """검색 결과 컨텍스트 메뉴"""
        item = self.search_results_table.itemAt(pos)
        if not item:
            return
        
        row = item.row()
        name_item = self.search_results_table.item(row, 0)
        if not name_item:
            return
        
        file_path = name_item.data(Qt.UserRole)
        if not file_path or not os.path.exists(file_path):
            return
        
        menu = QMenu(self)
        
        # 열기
        open_action = QAction("열기", self)
        open_action.triggered.connect(lambda: self.open_file(file_path) if os.path.isfile(file_path) else None)
        menu.addAction(open_action)
        
        # 폴더에서 보기
        show_in_folder_action = QAction("폴더에서 보기", self)
        show_in_folder_action.triggered.connect(lambda: self.show_in_explorer(file_path))
        menu.addAction(show_in_folder_action)
        
        menu.addSeparator()
        
        # 경로 복사
        copy_path_action = QAction("경로 복사", self)
        copy_path_action.triggered.connect(lambda: self.copy_path_to_clipboard(file_path))
        menu.addAction(copy_path_action)
        
        # 속성
        properties_action = QAction("속성", self)
        properties_action.triggered.connect(lambda: self.show_file_properties(file_path))
        menu.addAction(properties_action)
        
        menu.exec_(self.search_results_table.viewport().mapToGlobal(pos))
    
    def show_in_explorer(self, file_path):
        """탐색기에서 파일/폴더 위치 표시"""
        self.main_tabs.setCurrentIndex(0)  # 탐색기 탭으로 전환
        
        if os.path.isfile(file_path):
            # 파일인 경우 부모 디렉토리로 이동
            folder_path = os.path.dirname(file_path)
        else:
            folder_path = file_path
        
        current_tab = self.active_tab_widget.currentWidget()
        if current_tab and hasattr(current_tab, 'current_path'):
            self.navigate_to(current_tab, folder_path, self.active_side)
    
    def copy_path_to_clipboard(self, file_path):
        """경로를 클립보드에 복사"""
        normalized_path = self.normalize_path(file_path)
        formatted_path = f'"{normalized_path}"'
        QApplication.clipboard().setText(formatted_path)
        self.status_label.setText("경로 복사됨")
    
    def show_file_properties(self, file_path):
        """파일 속성 대화상자 표시"""
        dialog = FilePropertiesDialog(file_path, self)
        dialog.exec()
    
    def copy_full_path(self):
        """전체 경로 복사 (백슬래시 형식) - 파일 탐색기 또는 즐겨찾기에서 작동"""
        # 포커스가 즐겨찾기 위젯에 있는지 확인
        focused_widget = QApplication.focusWidget()
        
        if focused_widget == self.favorites or self.favorites.hasFocus():
            # 즐겨찾기에서 경로 복사
            current_item = self.favorites.currentItem()
            if current_item:
                item_text = current_item.text()
                if item_text in self.favorite_paths:
                    path = self.favorite_paths[item_text]
                    normalized_path = self.normalize_path(path)
                    QApplication.clipboard().setText(normalized_path)
                    self.status_label.setText("즐겨찾기 경로 복사됨")
                    print(f"복사된 즐겨찾기 경로:\n{normalized_path}")
                else:
                    self.status_label.setText("경로를 찾을 수 없음")
            return
        
        # 파일 탐색기에서 경로 복사 (기존 기능)
        current_tab = self.active_tab_widget.currentWidget()
        if not current_tab:
            return
        
        selected = current_tab.tree.selectionModel().selectedIndexes()
        if selected:
            paths = []
            for idx in selected:
                if idx.column() == 0:
                    # 프록시 인덱스를 소스 인덱스로 변환
                    source_index = current_tab.model.mapToSource(idx)
                    file_path = current_tab.source_model.filePath(source_index)
                    # 슬래시를 백슬래시로 변경 (큰따옴표 없이)
                    # normalize_path 사용하여 일관성 유지
                    normalized_path = self.normalize_path(file_path)
                    paths.append(normalized_path)
            
            if paths:
                result = "\n".join(paths)
                QApplication.clipboard().setText(result)
                self.status_label.setText(f"{len(paths)} 개 경로 복사됨")
                # 디버깅: 복사된 내용 확인
                print(f"복사된 경로:\n{result}")
    
    def copy_current_folder_path(self, tab):
        """현재 폴더 경로 복사"""
        if tab and hasattr(tab, 'current_path'):
            normalized_path = self.normalize_path(tab.current_path)
            QApplication.clipboard().setText(normalized_path)
            self.status_label.setText("경로 복사됨")
    
    def copy_path_to_clipboard_simple(self, path):
        """경로를 클립보드에 복사 (간단 버전)"""
        normalized_path = self.normalize_path(path)
        QApplication.clipboard().setText(normalized_path)
        self.status_label.setText("경로 복사됨")
    
    def load_memos(self):
        """메모 데이터 로드"""
        try:
            if self.memo_file.exists():
                with open(self.memo_file, 'r', encoding='utf-8') as f:
                    memos = json.load(f)
                
                # 공유 메모 딕셔너리에 로드
                self.shared_memos.clear()
                self.shared_memos.update(memos)
                
                # 모든 탭의 메모 컬럼 새로고침
                self.refresh_all_memo_columns()
                
                if hasattr(self, 'status_label'):
                    self.status_label.setText(f"메모 {len(memos)}개 로드됨")
        except Exception as e:
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"메모 로드 실패: {e}")
    
    def save_memos(self):
        """메모 데이터 저장"""
        try:
            # 공유 메모 딕셔너리를 직접 저장
            memos = self.shared_memos.copy()
            
            # 설정 폴더 생성
            self._config_dir().mkdir(parents=True, exist_ok=True)
            
            # JSON 파일로 저장
            with open(self.memo_file, 'w', encoding='utf-8') as f:
                json.dump(memos, f, ensure_ascii=False, indent=2)
            
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"메모 {len(memos)}개 저장됨")
        except Exception as e:
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"메모 저장 실패: {e}")
    
    def on_memo_changed(self, file_path):
        """메모가 변경되었을 때 모든 탭의 해당 파일 행 업데이트"""
        self.refresh_all_memo_columns()
    
    def refresh_all_memo_columns(self):
        """모든 탭의 메모 컬럼 새로고침"""
        for tab_widget in [self.left_tabs, self.right_tabs]:
            for i in range(tab_widget.count()):
                tab = tab_widget.widget(i)
                if tab and hasattr(tab, 'source_model') and hasattr(tab, 'tree'):
                    # 현재 표시된 디렉토리의 모든 항목에 대해 dataChanged 시그널 발생
                    root_index = tab.tree.rootIndex()
                    if root_index.isValid():
                        # 프록시 모델을 통해 source 인덱스 가져오기
                        source_root = tab.model.mapToSource(root_index)
                        row_count = tab.source_model.rowCount(source_root)
                        if row_count > 0:
                            # 메모 컬럼(4번)만 업데이트
                            first_memo = tab.source_model.index(0, 4, source_root)
                            last_memo = tab.source_model.index(row_count - 1, 4, source_root)
                            tab.source_model.dataChanged.emit(first_memo, last_memo)


# ====================================================================================
# 5. 프로그램 실행 진입점 (Program Entry Point)
# ====================================================================================
    # 프로그램의 시작점입니다.
    # - 고해상도 디스플레이 지원 설정
    # - QApplication 생성 및 초기화
    # - FileExplorer 인스턴스 생성 및 표시
    # - 이벤트 루프 실행

if __name__ == "__main__":
    # 고해상도 디스플레이 지원 설정 (QApplication 생성 전 필수) --------
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)        # DPI 스케일링 활성화
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)           # 고해상도 픽스맵 사용
    
    # 애플리케이션 생성 및 실행 -----------------------------------------
    app = QApplication(sys.argv)                                       # Qt 애플리케이션 생성
    
    window = FileExplorer()                                            # 메인 윈도우 인스턴스 생성
    window.show()                                                      # 윈도우 표시
    sys.exit(app.exec())                                                # 이벤트 루프 실행
