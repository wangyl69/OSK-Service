// Diagnostic input frontend. Never writes to HybridSED pipes or HID directly.
#include <windows.h>
#include <windowsx.h>
#include <shellapi.h>
#include <imm.h>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdio>
#include <stdexcept>
#include "touch_test_input.hpp"

namespace {
bool mouseTest=false,legacyTouch=false;
HIMC previousIme=nullptr;
touchtest::ContactGate gate;
std::ofstream output;
unsigned touches=0,sent=0,failed=0,received=0,ignoredMouse=0;
std::wstring echoed,last=L"等待手指点击蓝色区域";
std::filesystem::path logPath;
unsigned uiDpi=96;
int px(int value){return MulDiv(value,uiDpi,96);}
RECT pad(HWND window){RECT r{};GetClientRect(window,&r);r.left=px(24);r.right-=px(24);r.top=px(120);r.bottom-=px(180);return r;}
std::string timestamp(){SYSTEMTIME t{};GetSystemTime(&t);char b[48]{};snprintf(b,sizeof(b),"%04u-%02u-%02uT%02u:%02u:%02u.%03uZ",t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,t.wMilliseconds);return b;}
void log(const std::string& fields){if(output){output<<"{\"utc\":\""<<timestamp()<<"\",\"qpc\":";LARGE_INTEGER q{};QueryPerformanceCounter(&q);output<<q.QuadPart<<','<<fields<<"}\n";output.flush();}}
void drawLine(HDC dc,RECT r,const std::wstring& text,int size,COLORREF color=RGB(25,35,45)){
    const auto font=CreateFontW(-px(size),0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Microsoft YaHei UI");
    auto old=SelectObject(dc,font);SetBkMode(dc,TRANSPARENT);SetTextColor(dc,color);DrawTextW(dc,text.c_str(),-1,&r,DT_LEFT|DT_WORDBREAK|DT_NOPREFIX);SelectObject(dc,old);DeleteObject(font);
}
void paint(HWND window){
    PAINTSTRUCT ps{};auto dc=BeginPaint(window,&ps);RECT all{};GetClientRect(window,&all);FillRect(dc,&all,static_cast<HBRUSH>(GetStockObject(WHITE_BRUSH)));
    RECT r{px(24),px(16),all.right-px(24),px(58)};drawLine(dc,r,L"Touch → HybridSED → HybridService → MCU",24);
    r={px(24),px(57),all.right-px(24),px(115)};drawLine(dc,r,mouseTest?L"鼠标诊断模式：不证明触摸有效":(legacyTouch?L"真实 Touch / WM_TOUCH：每次按下触发一次":L"真实 Touch / WM_POINTER：每次按下触发一次"),20,mouseTest?RGB(160,90,0):RGB(0,100,90));
    const auto area=pad(window);auto brush=CreateSolidBrush(mouseTest?RGB(255,239,205):RGB(220,240,252));FillRect(dc,&area,brush);DeleteObject(brush);
    for(int i=0;i<3;++i){RECT key{area.left+(area.right-area.left)*i/3+px(12),area.top+px(12),area.left+(area.right-area.left)*(i+1)/3-px(12),area.bottom-px(12)};
        FrameRect(dc,&key,static_cast<HBRUSH>(GetStockObject(GRAY_BRUSH)));key.top+=(key.bottom-key.top)/3;key.left+=px(24);drawLine(dc,key,std::wstring(1,L"ASD"[i]),64);}
    r={px(24),all.bottom-px(166),all.right-px(24),all.bottom-px(130)};drawLine(dc,r,L"按下="+std::to_wstring(touches)+L"  Windows注入成功="+std::to_wstring(sent)+L"  失败="+std::to_wstring(failed),18);
    r={px(24),all.bottom-px(128),all.right-px(24),all.bottom-px(95)};drawLine(dc,r,L"收到标记键盘输入："+echoed,20);
    r={px(24),all.bottom-px(92),all.right-px(24),all.bottom-px(51)};drawLine(dc,r,last,16);
    r={px(24),all.bottom-px(49),all.right-px(24),all.bottom-px(6)};drawLine(dc,r,L"先间隔1秒测试。注入成功不代表LRA已振动；请结合SED/MCU日志。",15);
    EndPaint(window,&ps);
}
void down(HWND window,UINT32 id,touchtest::Source source,POINT point,bool cancelled){
    const auto area=pad(window);const auto vk=touchtest::keyAt(area,point);
    if(!gate.down(id,source,mouseTest,vk!=0,cancelled))return;
    ++touches;log("\"event\":\"contact_down\",\"sequence\":"+std::to_string(touches)+",\"pointer_id\":"+std::to_string(id)+",\"source\":\""+(source==touchtest::Source::Touch?"touch":"mouse-test")+"\",\"x\":"+std::to_string(point.x)+",\"y\":"+std::to_string(point.y)+",\"vk\":"+std::to_string(vk));
    SetForegroundWindow(window);SetFocus(window);
    if(GetForegroundWindow()!=window){++failed;last=L"无法取得前台焦点，未注入事件";log("\"event\":\"focus_failed\"");InvalidateRect(window,nullptr,FALSE);return;}
    if((GetAsyncKeyState(VK_CONTROL)|GetAsyncKeyState(VK_MENU)|GetAsyncKeyState(VK_SHIFT))&0x8000){++failed;last=L"请松开Ctrl/Alt/Shift后重试";log("\"event\":\"modifier_held\"");InvalidateRect(window,nullptr,FALSE);return;}
    SetLastError(0);const auto count=touchtest::emit(vk);const auto error=GetLastError();
    if(count==2){++sent;last=L"已发送带OSK标记的按下/抬起；等待SED与硬件验证";}
    else{++failed;last=L"Windows输入注入失败或仅部分成功，见日志";}
    log("\"event\":\"send_input\",\"sequence\":"+std::to_string(touches)+",\"vk\":"+std::to_string(vk)+",\"accepted_inputs\":"+std::to_string(count)+",\"win32_error\":"+std::to_string(error)+",\"marker\":\"0x5345445F4F534B\"");InvalidateRect(window,nullptr,FALSE);
}
LRESULT CALLBACK procedure(HWND window,UINT message,WPARAM wParam,LPARAM lParam){
    switch(message){
    case WM_CREATE:uiDpi=GetDpiForWindow(window);if(!uiDpi)uiDpi=96;
        // This window is an A/S/D diagnostic surface, not an IME editor.
        // Scope the change to this HWND; never change the system input layout.
        previousIme=ImmAssociateContext(window,nullptr);return 0;
    case WM_PAINT:paint(window);return 0;
    case WM_SIZE:InvalidateRect(window,nullptr,FALSE);return 0;
    case WM_GETMINMAXINFO:reinterpret_cast<MINMAXINFO*>(lParam)->ptMinTrackSize={px(600),px(520)};return 0;
    case WM_POINTERDOWN:case WM_POINTERUPDATE:case WM_POINTERUP:case WM_POINTERCAPTURECHANGED:{
        if(legacyTouch||mouseTest)break;
        const auto id=GET_POINTERID_WPARAM(wParam);
        if(message==WM_POINTERCAPTURECHANGED){gate.up(id);return 0;}
        POINTER_INFO info{};if(!GetPointerInfo(id,&info)){log("\"event\":\"pointer_info_failed\",\"error\":"+std::to_string(GetLastError()));return 0;}
        if(info.pointerType!=PT_TOUCH)break;
        if(message==WM_POINTERDOWN){auto point=info.ptPixelLocation;ScreenToClient(window,&point);down(window,id,touchtest::Source::Touch,point,(info.pointerFlags&POINTER_FLAG_CANCELED)!=0);}
        if(message==WM_POINTERUP||(info.pointerFlags&POINTER_FLAG_CANCELED))gate.up(id);
        return 0;}
    case WM_TOUCH:{
        if(!legacyTouch)break;
        const auto count=static_cast<UINT>(LOWORD(wParam));std::vector<TOUCHINPUT> inputs(count<=128?count:0);
        if(count&&count<=128&&GetTouchInputInfo(reinterpret_cast<HTOUCHINPUT>(lParam),count,inputs.data(),sizeof(TOUCHINPUT))){
            for(const auto& input:inputs){if(input.dwFlags&TOUCHEVENTF_DOWN){POINT p{TOUCH_COORD_TO_PIXEL(input.x),TOUCH_COORD_TO_PIXEL(input.y)};ScreenToClient(window,&p);down(window,input.dwID,touchtest::Source::Touch,p,false);}if(input.dwFlags&TOUCHEVENTF_UP)gate.up(input.dwID);}
        }else log("\"event\":\"legacy_touch_read_failed\",\"error\":"+std::to_string(GetLastError()));
        CloseTouchInputHandle(reinterpret_cast<HTOUCHINPUT>(lParam));return 0;}
    case WM_LBUTTONDOWN:
        if(mouseTest){gate.up(1);down(window,1,touchtest::Source::Mouse,{GET_X_LPARAM(lParam),GET_Y_LPARAM(lParam)},false);gate.up(1);}
        else{++ignoredMouse;log("\"event\":\"mouse_ignored\"");}return 0;
    case WM_KEYDOWN:
        if(static_cast<ULONG_PTR>(GetMessageExtraInfo())==touchtest::marker){++received;if(wParam=='A'||wParam=='S'||wParam=='D'){echoed+=static_cast<wchar_t>(wParam);if(echoed.size()>48)echoed.erase(0,echoed.size()-48);}
            log("\"event\":\"marked_key_received\",\"count\":"+std::to_string(received)+",\"vk\":"+std::to_string(wParam));InvalidateRect(window,nullptr,FALSE);return 0;}
        if(wParam==VK_ESCAPE){DestroyWindow(window);return 0;}break;
    case WM_KILLFOCUS:gate.reset();return 0;
    case WM_DPICHANGED:{uiDpi=HIWORD(wParam);auto r=reinterpret_cast<RECT*>(lParam);SetWindowPos(window,nullptr,r->left,r->top,r->right-r->left,r->bottom-r->top,SWP_NOZORDER|SWP_NOACTIVATE);return 0;}
    case WM_DESTROY:
        if(previousIme)ImmAssociateContext(window,previousIme);
        log("\"event\":\"summary\",\"contacts\":"+std::to_string(touches)+",\"injected_pairs\":"+std::to_string(sent)+",\"failed\":"+std::to_string(failed)+",\"marked_down_received\":"+std::to_string(received)+",\"mouse_ignored\":"+std::to_string(ignoredMouse));PostQuitMessage(0);return 0;
    }
    return DefWindowProcW(window,message,wParam,lParam);
}
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,PWSTR,int){
    try{
        int count=0;auto args=CommandLineToArgvW(GetCommandLineW(),&count);
        for(int i=1;i<count;++i){std::wstring option=args[i];if(option==L"--mouse-test")mouseTest=true;else if(option==L"--legacy-touch")legacyTouch=true;
            else if(option==L"--log-file"&&i+1<count)logPath=args[++i];else throw std::runtime_error("Unknown or incomplete argument");}
        LocalFree(args);if(mouseTest&&legacyTouch)throw std::runtime_error("Choose mouse-test or legacy-touch, not both");
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        if(logPath.empty())logPath=std::filesystem::path(L"logs")/(L"touch-test-"+std::to_wstring(GetCurrentProcessId())+L".jsonl");
        if(logPath.has_parent_path())std::filesystem::create_directories(logPath.parent_path());output.open(logPath,std::ios::app);if(!output)throw std::runtime_error("Cannot open touch test log");
        LARGE_INTEGER frequency{};QueryPerformanceFrequency(&frequency);
        log("\"event\":\"start\",\"mode\":\""+std::string(mouseTest?"mouse-test":legacyTouch?"touch-legacy":"touch-pointer")+"\",\"qpc_frequency\":"+std::to_string(frequency.QuadPart)+",\"marker\":\"0x5345445F4F534B\",\"direct_pipe_or_hid\":false");
        WNDCLASSW cls{};cls.hInstance=instance;cls.lpfnWndProc=procedure;cls.lpszClassName=L"AthenaTouchHapticTest";cls.hCursor=LoadCursor(nullptr,IDC_ARROW);RegisterClassW(&cls);
        uiDpi=GetDpiForSystem();if(!uiDpi)uiDpi=96;
        RECT work{};SystemParametersInfoW(SPI_GETWORKAREA,0,&work,0);
        const int width=std::min(px(900),static_cast<int>(work.right-work.left-px(40))),height=std::min(px(720),static_cast<int>(work.bottom-work.top-px(60)));
        auto window=CreateWindowExW(0,cls.lpszClassName,L"Athena Touch 链路测试",WS_OVERLAPPEDWINDOW,work.left+20,work.top+20,width,height,nullptr,nullptr,instance,nullptr);
        if(!window)throw std::runtime_error("Cannot create test window");
        if(legacyTouch&&!RegisterTouchWindow(window,0))throw std::runtime_error("RegisterTouchWindow failed");
        ShowWindow(window,SW_SHOW);UpdateWindow(window);MSG message{};while(GetMessageW(&message,nullptr,0,0)>0){TranslateMessage(&message);DispatchMessageW(&message);}return 0;
    }catch(const std::exception& error){MessageBoxA(nullptr,error.what(),"Touch test startup failed",MB_OK|MB_ICONERROR);return 1;}
}
